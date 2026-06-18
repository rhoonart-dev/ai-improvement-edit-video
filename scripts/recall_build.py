#!/usr/bin/env python3
"""② recall 벤치마크 — ai-video가 '검증된 사람 히트'에 가까운 토픽을 골랐나? → recall_benchmark.

데이터 한계(정직): 사람 쇼츠의 *source 구간*(롱폼 어디서 잘렸나)이 없어 시간축 IoU/segment recall은
불가(핑거프린팅 필요). 대신 **토픽 recall** — ai-video 쇼츠 제목 ↔ 같은 채널 사람 히트(조회수 상위)
제목 유사도(reconcile_published.title_similarity 재사용).
  recall_at_k     = top-k 사람 히트와의 *최대* 제목 유사도 (0~1)
  weighted_recall = 전체 사람 클립 제목 유사도의 조회수 가중 평균
  iou/boundary_err_sec = NULL (시간축 recall 미적용; holdout_set 라벨에 명시)

env: PIPELINE_DB_URL(xxon), LAEEBLY_DB_URL(사람 제목·조회수)
실행:
  ... python scripts/recall_build.py --work "로맨스의 절댓값" --channel "스토리순삭" --k 10
"""
from __future__ import annotations

import argparse
import os
import sys

from reconcile_published import title_similarity

HOLDOUT = "title_topic_v1"   # 의미적 토픽 recall (시간축 아님 — source 정렬 필요)


# ─────────────────────────── 순수 메트릭 (단위테스트) ───────────────────────────

def topic_recall(av_title, humans, k=10):
    """av_title vs humans[(title, views)] → 토픽 recall 메트릭.
    recall_at_k = 조회수 top-k 사람 히트와의 최대 유사도. weighted_recall = 조회수 가중 평균 유사도."""
    humans = [(t, (v or 0)) for t, v in humans if t]
    if not humans:
        return {"recall_at_k": None, "weighted_recall": None, "k": 0, "nearest": None}
    ranked = sorted(humans, key=lambda h: h[1], reverse=True)
    hits = ranked[:k]
    sims_hits = [(title_similarity(av_title, t), t) for t, _ in hits]
    best_sim, best_title = max(sims_hits, key=lambda x: x[0]) if sims_hits else (0.0, None)
    tot = sum(v for _, v in humans) or 1
    wrec = sum(title_similarity(av_title, t) * v for t, v in humans) / tot
    return {"recall_at_k": round(best_sim, 4), "weighted_recall": round(wrec, 4),
            "k": len(hits), "nearest": best_title}


# ─────────────────────────── DB ───────────────────────────

def fetch_aivideo_clips(pconn, work):
    from psycopg.rows import dict_row
    with pconn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            select c.id as clip_id, c.work_id,
                   (m.edit_plan->'layout'->>'top_title') as title
            from clips c
            join clip_metadata m on m.clip_id = c.id
            join works w on w.id = c.work_id
            where c.source='auto_edit' and w.title = %s
        """, (work,))
        return cur.fetchall()


def fetch_human_titles(lconn, channel, limit=80):
    """laeebly youtube_studio: 채널의 사람 쇼츠 제목 + 조회수(content_id별 최신)."""
    from psycopg.rows import dict_row
    with lconn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            select distinct on (btrim(content_id)) video_title, views
            from public.youtube_studio
            where channel_name = %s and video_title is not null and btrim(video_title) <> ''
            order by btrim(content_id), upload_at desc
        """, (channel,))
        rows = cur.fetchall()
    rows.sort(key=lambda r: (r["views"] or 0), reverse=True)
    return [(r["video_title"], r["views"]) for r in rows[:limit]]


def write_recall(pconn, work_id, k, m):
    with pconn.cursor() as cur:
        cur.execute("""
            insert into recall_benchmark(work_id, holdout_set, recall_at_k, k, weighted_recall)
            values (%s, %s, %s, %s, %s) returning id
        """, (work_id, HOLDOUT, m["recall_at_k"], k, m["weighted_recall"]))
        rid = cur.fetchone()[0]
    pconn.commit()
    return rid


def main():
    ap = argparse.ArgumentParser(description="recall 벤치마크 (토픽) → recall_benchmark")
    ap.add_argument("--work", required=True, help="xxon works.title (ai-video 클립 소속)")
    ap.add_argument("--channel", required=True, help="laeebly 사람 히트 채널명")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--limit-humans", type=int, default=80)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import psycopg
    pconn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    lconn = psycopg.connect(os.environ["LAEEBLY_DB_URL"])
    try:
        clips = fetch_aivideo_clips(pconn, args.work)
        humans = fetch_human_titles(lconn, args.channel, args.limit_humans)
        print(f"ai-video clips: {len(clips)} | 사람 히트 reference: {len(humans)} (채널 {args.channel})")
        if not clips or not humans:
            sys.exit("ai-video 클립 또는 사람 reference 없음")
        for c in clips:
            m = topic_recall(c["title"], humans, args.k)
            print(f"  clip {str(c['clip_id'])[:8]} title={ (c['title'] or '')[:30]!r} → "
                  f"recall@{m['k']}={m['recall_at_k']} wrecall={m['weighted_recall']} "
                  f"nearest={ (m['nearest'] or '')[:34]!r}")
            if not args.dry_run:
                rid = write_recall(pconn, c["work_id"], m["k"], m)
                print(f"    → recall_benchmark {str(rid)[:8]}")
    finally:
        pconn.close(); lconn.close()


if __name__ == "__main__":
    main()
