#!/usr/bin/env python3
"""
M0 ETL — laeebly.youtube_studio (prod, READ-ONLY) → video-improvement-pipeline (격리, fdidiqdhcyctdbogxkdu)

설계: docs/M0_ETL.md · docs/SELF_IMPROVEMENT_SPEC.md. 순수 변환 규칙은 etl_transforms.py(테스트됨).
- 멱등(재실행 안전): channels/clips=ON CONFLICT, works=존재확인 후 insert, performance=ON CONFLICT DO UPDATE.
- PII/매출 컬럼 미복사: email · profits_krw · *_revenue · shopping_* 는 SELECT 하지 않음.
- 성과 = 일별 증분을 +{1,3,7,14}일 윈도우로 SUM. 14 = y 확정 스냅샷.
- 데이터 품질(검증서 발견): content_id 선행/후행 공백 btrim, 빈 licensed_video_title 제외.

환경변수:
  LAEEBLY_DB_URL    laeebly 연결 문자열(읽기 권한)
  PIPELINE_DB_URL   격리 프로젝트 연결 문자열(쓰기)
실행:
  pip install "psycopg[binary]>=3.1"
  LAEEBLY_DB_URL=... PIPELINE_DB_URL=... python scripts/etl_laeebly_to_pipeline.py
"""
import os

import psycopg
from psycopg.rows import dict_row

from etl_transforms import is_short, min_days_for_window, perf_values

WINDOWS = [1, 3, 7, 14]       # 14 = y 확정, 나머지 = 조기속도


def q(conn, sql, params=None):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def upsert_channels(dst, rows):
    with dst.cursor() as cur:
        cur.executemany(
            """INSERT INTO public.channels (platform, channel_external_id, name, subscriber_count)
               VALUES ('youtube', %s, %s, %s)
               ON CONFLICT (platform, channel_external_id)
               DO UPDATE SET name = EXCLUDED.name, subscriber_count = EXCLUDED.subscriber_count""",
            [(r["channel_id"], r["channel_name"], r["subscribers"]) for r in rows],
        )
    dst.commit()
    return {r["channel_external_id"]: r["id"] for r in
            q(dst, "SELECT id, channel_external_id FROM public.channels WHERE platform='youtube'")}


def upsert_works(dst, rows):
    have = {r["title"] for r in q(dst, "SELECT title FROM public.works")}
    new = [(r["title"],) for r in rows if r["title"] not in have]
    if new:
        with dst.cursor() as cur:
            cur.executemany("INSERT INTO public.works (title, content_type) VALUES (%s, NULL)", new)
        dst.commit()
    return {r["title"]: r["id"] for r in q(dst, "SELECT id, title FROM public.works")}


def upsert_clips(dst, rows, chan, work):
    payload = [(
        work.get(r["work_title"]),         # NULL 허용(작품 미상)
        chan.get(r["channel_id"]),
        r["content_id"],
        r["len_sec"],
        r["publish_time"],
        is_short(r["len_sec"]),
    ) for r in rows]
    with dst.cursor() as cur:
        # DO NOTHING → published_at 만 보수적 DO UPDATE: ingest 가 먼저 만든 auto_edit 행
        # (published_at NULL)을 laeebly publish_time 으로 백필 — R5(§4-3)·판정 창의 근거.
        # 이미 값이 있으면 laeebly 값(원천)을 우선하되 NULL 로 덮지 않는다. 나머지 컬럼 불변.
        cur.executemany(
            """INSERT INTO public.clips
                 (work_id, channel_id, video_external_id, source, duration_sec, published_at, is_format_short)
               VALUES (%s, %s, %s, 'existing', %s, %s, %s)
               ON CONFLICT (video_external_id) DO UPDATE
                 SET published_at = COALESCE(EXCLUDED.published_at, clips.published_at)""",
            payload,
        )
    dst.commit()
    return {r["video_external_id"]: r["id"] for r in
            q(dst, "SELECT id, video_external_id FROM public.clips WHERE video_external_id IS NOT NULL")}


def load_perf(src, dst, clip, n):
    rows = q(src, """
        SELECT btrim(content_id)                      AS content_id,
               sum(views)                            AS views,
               sum(watch_time_hours)                 AS wt_hours,
               sum(impressions)                      AS impressions,
               avg(NULLIF(impression_click_rate, 0)) AS ctr,
               max(CASE WHEN video_length ~ '^[0-9.]+$' THEN video_length::numeric END) AS len_sec,
               count(*)                              AS days_covered
        FROM public.youtube_studio
        WHERE content_id IS NOT NULL AND btrim(content_id) <> ''
          AND upload_at::date BETWEEN publish_time::date AND (publish_time::date + %s)
        GROUP BY btrim(content_id)
        HAVING count(*) >= %s
    """, (n, min_days_for_window(n)))
    payload = []
    for r in rows:
        cid = clip.get(r["content_id"])
        if cid is None:
            continue
        v, apv, imp, ctr = perf_values(r["views"], r["wt_hours"], r["impressions"], r["ctr"], r["len_sec"])
        payload.append((cid, n, v, apv, imp, ctr))
    with dst.cursor() as cur:
        cur.executemany(
            """INSERT INTO public.clip_performance
                 (clip_id, snapshot_window_days, views, avg_view_pct, impressions, ctr, data_source)
               VALUES (%s, %s, %s, %s, %s, %s, 'youtube_studio')
               ON CONFLICT (clip_id, snapshot_window_days)
               DO UPDATE SET views = EXCLUDED.views, avg_view_pct = EXCLUDED.avg_view_pct,
                             impressions = EXCLUDED.impressions, ctr = EXCLUDED.ctr,
                             collected_at = now()""",
            payload,
        )
    dst.commit()
    return len(payload)


def main():
    src = psycopg.connect(os.environ["LAEEBLY_DB_URL"], autocommit=True)   # 읽기 전용 사용
    dst = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    try:
        chan = upsert_channels(dst, q(src, """
            SELECT DISTINCT ON (channel_id) channel_id, channel_name, subscribers
            FROM public.youtube_studio WHERE channel_id IS NOT NULL
            ORDER BY channel_id, upload_at DESC"""))
        print(f"channels: {len(chan)}")

        work = upsert_works(dst, q(src, """
            SELECT btrim(licensed_video_title) AS title
            FROM public.youtube_studio
            WHERE licensed_video_title IS NOT NULL AND btrim(licensed_video_title) <> ''
            GROUP BY btrim(licensed_video_title)"""))
        print(f"works: {len(work)}")

        clip = upsert_clips(dst, q(src, """
            SELECT btrim(content_id)                       AS content_id,
                   max(channel_id)                         AS channel_id,
                   max(NULLIF(btrim(licensed_video_title), '')) AS work_title,
                   min(publish_time)                       AS publish_time,
                   max(CASE WHEN video_length ~ '^[0-9.]+$' THEN video_length::numeric END) AS len_sec
            FROM public.youtube_studio
            WHERE content_id IS NOT NULL AND btrim(content_id) <> ''
            GROUP BY btrim(content_id)"""), chan, work)
        print(f"clips: {len(clip)}")

        for n in WINDOWS:
            print(f"perf +{n}d: {load_perf(src, dst, clip, n)}")
    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    main()
