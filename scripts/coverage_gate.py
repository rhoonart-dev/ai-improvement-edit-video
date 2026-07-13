#!/usr/bin/env python3
"""커버리지 게이트(§3-6) — 판정·감사 트리거를 벽시계가 아니라 데이터 커버리지로.

factory GATE_SQL 패턴(factory/db.py) 이식: laeebly youtube_studio 에서
  max(upload_at) >= publish_time + window  (upload_at = delta 행의 '데이터 날짜')
를 코호트 content_id 별로 검증. ETL 의 clip_performance(+7d) 행은 커버리지 4일(0.7×7)만
돼도 생기므로(etl_transforms.min_days_for_window) 행 존재만으로 판정하면 조기 판정 위험 —
이 게이트가 그 구멍을 막는다.

용법(loop_controller measure / decide_experiment 에서):
    from coverage_gate import uncovered
    bad = uncovered(laeebly_conn, ids, window_days=7)
    if bad: ...판정 보류...
env: LAEEBLY_DB_URL (읽기전용)
"""
from __future__ import annotations

# upload_at 기준 커버리지: publish 후 +window 일자 데이터가 실제로 적재됐는가.
# (min(upload_at) 좌측절단 체크는 factory 게이트 담당 — 여기선 판정 성숙만 본다.)
COVERAGE_SQL = """
    WITH pv AS (
      SELECT btrim(content_id) AS content_id,
             min(publish_time) AS publish_time,
             max(upload_at)    AS last_data
      FROM youtube_studio
      WHERE btrim(content_id) = ANY(%(ids)s)
      GROUP BY 1
    )
    SELECT content_id,
           (publish_time IS NOT NULL
            AND last_data >= publish_time + make_interval(days => %(window_days)s)) AS covered
    FROM pv
"""


# ─────────────────────────── 순수 (단위테스트) ───────────────────────────
def split_covered(ids, rows):
    """(covered_ids, uncovered_ids). rows = COVERAGE_SQL 결과 dict 목록.
       조회 안 된 id(laeebly 에 행 자체가 없음)는 미커버 취급(보수적)."""
    got = {r["content_id"]: bool(r.get("covered")) for r in rows}
    covered = [i for i in ids if got.get(i)]
    uncov = [i for i in ids if not got.get(i)]
    return covered, uncov


# ─────────────────────────── I/O ───────────────────────────
def uncovered(laeebly_conn, ids, window_days):
    """+window 미성숙 content_id 목록(빈 리스트 = 전부 커버, 판정 가능)."""
    from psycopg.rows import dict_row
    with laeebly_conn.cursor(row_factory=dict_row) as cur:
        cur.execute(COVERAGE_SQL, {"ids": list(ids), "window_days": window_days})
        rows = cur.fetchall()
    return split_covered(ids, rows)[1]


def connect_laeebly():
    """LAEEBLY_DB_URL 로 읽기전용 연결. 미설정이면 None(게이트 스킵은 호출자가 결정)."""
    import os
    dsn = os.environ.get("LAEEBLY_DB_URL")
    if not dsn:
        return None
    import psycopg
    conn = psycopg.connect(dsn)
    conn.read_only = True
    return conn
