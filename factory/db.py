#!/usr/bin/env python
"""DB 접근 계층.

- Laeebly : 원천(prod) Postgres 직결 — **읽기전용 세션 강제**. 게이트/성과창/통제 집계 SQL.
- Pipeline: 적재 DB(video-improvement-pipeline) PostgREST + Storage. stdlib urllib(의존성 0).

⚠ laeebly 컬럼 의미 (2026-07-07 실측):
    upload_at  = delta 행의 '데이터 날짜' (발행 당일부터 존재)
    created_at = DB 적재 시각 (~4일 지연) — 게이트·창 집계에 쓰면 오판
"""
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from decimal import Decimal

from config import (GATE_FIRST_DATA_TOL_DAYS, PERF_WINDOW_DAYS, REST_CHUNK,
                    SHORTS_MAX_SEC, SQL_ID_CHUNK)


def jsonable(v):
    if isinstance(v, Decimal):
        # Postgres SUM/COUNT는 numeric→Decimal로 온다. 정수값이면 int로 보내야
        # bigint 컬럼(views·impressions 등)이 받는다("0.0"이면 400 거부).
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def clean_row(d: dict) -> dict:
    return {k: jsonable(v) for k, v in d.items()}


# ═══════════════════════════════════════════════════════════════════
# laeebly (읽기전용)
# ═══════════════════════════════════════════════════════════════════
class Laeebly:
    def __init__(self, dsn: str):
        if not dsn:
            raise SystemExit(
                "LAEEBLY_DB_URL 없음 — factory/.env 에 laeebly Session-pooler DSN을 넣으세요.\n"
                "  (Supabase 대시보드 > laeebly > Connect > Session pooler URI)")
        try:
            import psycopg2
            import psycopg2.extras
        except ImportError:
            raise SystemExit("psycopg2 필요: pip install psycopg2-binary")
        self._extras = psycopg2.extras
        self.cx = psycopg2.connect(dsn)
        self.cx.set_session(readonly=True, autocommit=True)   # ★ prod 안전장치
        with self.cx.cursor() as cur:
            cur.execute("set time zone 'Asia/Seoul'")          # 크롤 데이터 일자 의미와 정렬

    def q(self, sql: str, params=None) -> list[dict]:
        with self.cx.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [clean_row(dict(r)) for r in cur.fetchall()]

    # ── 게이트: +7D 커버리지 + 쇼츠 길이 + 쇼츠 판별(impressions>0) 통과 후보 ──
    #    길이 초과·null = 롱폼/중간 → 제외.
    #    impressions=0 = 커뮤니티 게시물/이미지슬라이드(노출 개념 없음) → 제외.
    #      (실측: 게이트 통과 중 impressions=0은 0.3%(95개), 표본 45/45로 쇼츠↔게시물 완벽 분리.
    #       쇼츠는 피드 노출이 잡히지만 게시물은 0. youtube_studio는 콘텐츠 종류를 구분 안 하므로
    #       이 신호가 유일한 SQL측 쇼츠 판별자.)
    GATE_SQL = f"""
        with pv as (
          select btrim(content_id)  as content_id,
                 min(publish_time)  as publish_time,
                 min(upload_at)     as first_data,
                 max(upload_at)     as last_data,
                 sum(impressions)   as total_impr,
                 max(case when video_length ~ '^[0-9.]+$'
                          then video_length::numeric end) as len_sec
          from youtube_studio
          where btrim(content_id) <> ''
          group by 1
        )
        select content_id, publish_time,
               round((extract(epoch from (last_data - publish_time)) / 86400.0)::numeric, 1)
                 as gate_coverage_days
        from pv
        where publish_time is not null
          and len_sec <= {SHORTS_MAX_SEC}
          and coalesce(total_impr, 0) > 0
          and first_data <= publish_time + interval '{GATE_FIRST_DATA_TOL_DAYS} days'
          and last_data  >= publish_time + interval '{PERF_WINDOW_DAYS} days'
        order by publish_time desc
    """

    def gate_candidates(self) -> list[dict]:
        return self.q(self.GATE_SQL)

    # ── 채널 완전 기저: laeebly 전체 이력에서 채널별 쇼츠 +7D 조회수 ──────
    #    lift 분모용. 우리 적재분(eb_shorts_features)이 아니라 laeebly 원천의
    #    '그 채널 모든 쇼츠(≤SHORTS_MAX_SEC, +7D 성숙)'를 반환 → 완전한 채널 norm.
    #    trailing 창·자신 제외는 scoring이 in-memory로 처리(publish_time 기준).
    CHAN_BASELINE_SQL = f"""
        with ch_shorts as (
          select btrim(content_id) as cid, channel_id,
                 min(publish_time) as pt,
                 min(upload_at) as first_data, max(upload_at) as last_data,
                 sum(impressions) as total_impr,
                 max(case when video_length ~ '^[0-9.]+$'
                          then video_length::numeric end) as len_sec
          from youtube_studio
          where channel_id = any(%s) and btrim(content_id) <> ''
          group by 1, channel_id
        ),
        elig as (   -- ≤길이 + +7D 성숙 + 쇼츠판별(impressions>0, 게시물 제외)
          select cid, channel_id, pt from ch_shorts
          where len_sec <= {SHORTS_MAX_SEC} and pt is not null
            and coalesce(total_impr, 0) > 0
            and first_data <= pt + interval '{GATE_FIRST_DATA_TOL_DAYS} days'
            and last_data  >= pt + interval '{PERF_WINDOW_DAYS} days'
        )
        select e.channel_id, e.cid as shorts_id, min(e.pt) as publish_time,
               sum(ys.views) as views       -- +7D Σdelta
        from elig e
        join youtube_studio ys on btrim(ys.content_id) = e.cid
        where ys.upload_at >= e.pt
          and ys.upload_at <= e.pt + interval '{PERF_WINDOW_DAYS} days'
        group by e.channel_id, e.cid
    """

    def channel_baseline_shorts(self, channel_ids: list[str]) -> list[dict]:
        ids = sorted({c for c in channel_ids if c})
        out = []
        for i in range(0, len(ids), SQL_ID_CHUNK):
            out.extend(self.q(self.CHAN_BASELINE_SQL, (ids[i:i + SQL_ID_CHUNK],)))
        return out

    # ── +7D 창 성과 (Σdelta + rate 가중평균 재계산) ─────────────────────
    PERF_SQL = f"""
        with base as (
          select btrim(content_id) as cid, ys.*
          from youtube_studio ys
          where btrim(content_id) = any(%s)
        ),
        pv as (select cid, min(publish_time) as pt from base group by 1),
        w as (
          select b.* , pv.pt
          from base b join pv using (cid)
          where b.upload_at >= pv.pt
            and b.upload_at <= pv.pt + interval '{PERF_WINDOW_DAYS} days'
        )
        select cid as content_id,
               min(pt)                            as publish_time,
               min(created_at)                    as first_snapshot_at,
               max(created_at)                    as last_snapshot_at,
               count(*)                           as n_snapshots,
               sum(impressions)                   as impressions,
               round(sum(impression_click_rate * impressions)
                       filter (where impression_click_rate is not null)
                     / nullif(sum(impressions)
                       filter (where impression_click_rate is not null), 0), 4) as ctr_pct,
               sum(views)                         as views,
               sum(valid_views)                   as valid_views,
               round(sum(watch_time_hours)::numeric, 4) as watch_time_hours,
               round(sum(average_view_percentage * views)
                       filter (where average_view_percentage is not null)
                     / nullif(sum(views)
                       filter (where average_view_percentage is not null), 0), 4) as avg_view_percentage,
               round(sum(kept_watching_rate * views)
                       filter (where kept_watching_rate is not null)
                     / nullif(sum(views)
                       filter (where kept_watching_rate is not null), 0), 4) as kept_watching_rate,
               sum(likes)                         as likes,
               sum(dislikes)                      as dislikes,
               case when (sum(likes) + sum(dislikes)) > 0
                    then round(sum(likes)::numeric / (sum(likes) + sum(dislikes)), 4)
                    else null end                 as likes_ratio,
               sum(shares)                        as shares,
               sum(comments_added)                as comments_added,
               sum(subscribers)                   as subscribers_gained,
               round(sum(average_views_per_viewer * views)
                       filter (where average_views_per_viewer is not null)
                     / nullif(sum(views)
                       filter (where average_views_per_viewer is not null), 0), 4) as avg_views_per_viewer,
               sum(youtube_premium_views)         as youtube_premium_views
        from w
        group by cid
    """

    def perf_window(self, ids: list[str]) -> dict[str, dict]:
        out = {}
        for i in range(0, len(ids), SQL_ID_CHUNK):
            for r in self.q(self.PERF_SQL, (ids[i:i + SQL_ID_CHUNK],)):
                out[r["content_id"]] = r
        return out

    # ── 통제 covariate (clip_performance_v0.4.sql §3 포팅 + video_title) ──
    #    identification_code: youtube_studio 에 없어도 제목으로 licensed_video 가 붙으면
    #    v.identification_code 로 회수(coalesce) — 제목-연결 클립이 코드-키 모집단에 합류(§3-1①).
    #    단 제목이 여러 원작과 매치되면(n_match>1, 동명작·리메이크) 회수 포기(NULL 유지) —
    #    임의 코드가 모집단 키가 되는 오귀속 방지. order by 는 n_match=1 판정과 무관한 안정성용.
    CONTROL_SQL = """
        with ys as (
          select btrim(content_id) as content_id,
                 min(publish_time)                                          as publish_time,
                 (array_agg(channel_id   order by upload_at desc))[1]       as channel_id,
                 (array_agg(channel_name order by upload_at desc))[1]       as channel_name,
                 (array_agg(video_title  order by upload_at desc)
                    filter (where nullif(btrim(video_title),'') is not null))[1] as title_text,
                 max(nullif(btrim(identification_code), ''))                as identification_code,
                 max(nullif(btrim(licensed_video_title), ''))               as licensed_video_title,
                 max(case when video_length ~ '^[0-9.]+$' then video_length end) as video_length
          from youtube_studio
          where btrim(content_id) = any(%s)
          group by 1
        ),
        chan as (
          select distinct on (channel_id)
                 channel_id, subscriber_count, view_count, video_count
          from youtube_channel_snapshot
          order by channel_id, snapshot_date desc
        )
        select ys.content_id, ys.publish_time,
               extract(dow from ys.publish_time)::int as published_dow,
               ys.channel_id, ys.channel_name, ys.title_text,
               c.subscriber_count,
               c.view_count  as channel_total_views,
               c.video_count as channel_video_count,
               coalesce(ys.identification_code,
                        case when lv.n_match = 1 then lv.identification_code end)
                 as identification_code,
               ys.licensed_video_title,
               lv.video_type, lv.genre, lv.nation, lv.published_year, lv.cast_text,
               lv.canonical_title, ys.video_length
        from ys
        left join lateral (
          select v.identification_code, v.video_type, v.genre, v.nation, v.published_year,
                 v."cast" as cast_text, v.title as canonical_title,
                 count(*) over () as n_match
          from licensed_video v
          where v.identification_code = ys.identification_code
             or (ys.identification_code is null and v.title = ys.licensed_video_title)
          order by v.identification_code
          limit 1
        ) lv on true
        left join chan c on c.channel_id = ys.channel_id
    """

    def control(self, ids: list[str]) -> dict[str, dict]:
        out = {}
        for i in range(0, len(ids), SQL_ID_CHUNK):
            for r in self.q(self.CONTROL_SQL, (ids[i:i + SQL_ID_CHUNK],)):
                out[r["content_id"]] = r
        return out

    # ── 채널 최신 누적 스냅샷 (eb_channels L1 원천) ─────────────────────
    CHAN_SQL = """
        select distinct on (channel_id)
               channel_id, subscriber_count, view_count, video_count, snapshot_date
        from youtube_channel_snapshot
        where channel_id = any(%s)
        order by channel_id, snapshot_date desc
    """

    def channel_snapshots(self, channel_ids: list[str]) -> dict[str, dict]:
        out = {}
        ids = [c for c in channel_ids if c]
        for i in range(0, len(ids), SQL_ID_CHUNK):
            for r in self.q(self.CHAN_SQL, (ids[i:i + SQL_ID_CHUNK],)):
                out[r["channel_id"]] = r
        return out


# ═══════════════════════════════════════════════════════════════════
# pipeline (적재 DB) — PostgREST + Storage
# ═══════════════════════════════════════════════════════════════════
class Pipeline:
    def __init__(self, url: str, service_key: str):
        if not (url and service_key):
            raise SystemExit("PIPELINE_URL / PIPELINE_SERVICE_KEY 없음 (.env 확인)")
        self.url = url.rstrip("/")
        self.key = service_key

    def _req(self, method: str, path: str, body=None, headers=None, timeout=120):
        h = {"Authorization": f"Bearer {self.key}", "apikey": self.key,
             "Content-Type": "application/json"}
        h.update(headers or {})
        data = json.dumps(body, ensure_ascii=False, default=jsonable).encode() \
            if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:800]   # PostgREST 상세 에러 노출
            raise RuntimeError(f"{e.code} {path.split('?')[0]} — {body}") from None
        return json.loads(raw) if raw else None

    # 페이지네이션 SELECT (Range 헤더)
    def select(self, table: str, params: dict | None = None, page=1000) -> list[dict]:
        qs = urllib.parse.urlencode(params or {}, quote_via=urllib.parse.quote)
        rows, offset = [], 0
        while True:
            got = self._req("GET", f"/rest/v1/{table}?{qs}", headers={
                "Range-Unit": "items", "Range": f"{offset}-{offset + page - 1}"})
            rows.extend(got)
            if len(got) < page:
                return rows
            offset += page

    def upsert(self, table: str, rows: list[dict], on_conflict: str):
        for i in range(0, len(rows), REST_CHUNK):
            self._req("POST", f"/rest/v1/{table}?on_conflict={on_conflict}",
                      body=rows[i:i + REST_CHUNK],
                      headers={"Prefer": "resolution=merge-duplicates,return=minimal"})

    def insert_returning(self, table: str, row: dict) -> dict:
        got = self._req("POST", f"/rest/v1/{table}", body=[row],
                        headers={"Prefer": "return=representation"})
        return got[0]

    def update(self, table: str, match: dict, payload: dict):
        qs = "&".join(f"{k}=eq.{urllib.parse.quote(str(v))}" for k, v in match.items())
        self._req("PATCH", f"/rest/v1/{table}?{qs}", body=payload,
                  headers={"Prefer": "return=minimal"})

    # Storage 업로드 (x-upsert: 멱등)
    def storage_upload(self, bucket: str, object_path: str, file_path) -> int:
        from pathlib import Path
        data = Path(file_path).read_bytes()
        ctype = "video/webm" if str(file_path).lower().endswith(".webm") else "video/mp4"
        req = urllib.request.Request(
            f"{self.url}/storage/v1/object/{bucket}/{object_path}", data=data, method="POST",
            headers={"Authorization": f"Bearer {self.key}", "apikey": self.key,
                     "Content-Type": ctype, "x-upsert": "true"})
        with urllib.request.urlopen(req, timeout=600) as r:
            r.read()
        return len(data)
