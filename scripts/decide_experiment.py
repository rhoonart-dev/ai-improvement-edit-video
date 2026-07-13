#!/usr/bin/env python3
"""⑤ 실험 승격 — 챔피언-챌린저를 **벤치마크 백분위(발행 후 실측)**로 판정 → experiments 갱신.

챌린저가 마진 넘게 이기면 status='running', 못 이기면 'retired'. 기본 신호 = benchmark
(같은 작품 시장 대비 **+7일** 시청유지 백분위 — §3-6/D2. +14d 는 감사용 --window-days 14) —
증거상 judge 점수는 성과와 무상관이라 승격에 쓰지 않는다. judge 는 빠른 오프라인 프록시로만
옵션 제공(--metric judge). benchmark 점수가 아직 없으면(창 미적재) 판정을 보류한다.

env: PIPELINE_DB_URL [, LAEEBLY_DB_URL(커버리지 게이트)]
실행:
  ... python scripts/decide_experiment.py --experiment <uuid> [--metric benchmark|judge]
        [--margin 0.03] [--window-days 7]
"""
from __future__ import annotations

import argparse
import os
import sys


def pick_winner(scored, margin=0.0):
    """scored: {label: score|None}. (winner_label, reason). 무승부/결측이면 (None, 이유)."""
    vals = [(k, v) for k, v in scored.items() if v is not None]
    if len(vals) < 2:
        return None, "점수 부족(2개 미만)"
    vals.sort(key=lambda x: x[1], reverse=True)
    if vals[0][1] - vals[1][1] <= margin:
        return None, f"무승부(차 {round(vals[0][1] - vals[1][1], 4)} ≤ {margin})"
    return vals[0][0], f"{vals[0][0]} 승 ({vals[0][1]} > {vals[1][1]})"


def _latest_judge(conn, clip_id):
    """오프라인 빠른 프록시 — judge quality(성과예측 아님)."""
    with conn.cursor() as cur:
        cur.execute("SELECT quality_score FROM public.judge_runs WHERE clip_id=%s "
                    "ORDER BY created_at DESC LIMIT 1", (clip_id,))
        r = cur.fetchone()
    return float(r[0]) if r and r[0] is not None else None


def _benchmark_score(conn, clip_id, window_days=None):
    """승격의 올바른 신호 — 클립의 '같은 작품 시장(휴먼) 대비' 시청유지 백분위(0~1).
    창 기본 +7d(§3-6/D2, m3 DEFAULT_WINDOW_DAYS). 성과 미적재면 None(판정 보류).
    시장 비교군에서 모든 ai-video 산출물 제외(comparator_exclude_db — 레거시∪동적, §3-3)."""
    from m3_aivideo_benchmark import (DEFAULT_WINDOW_DAYS, comparator_exclude_db,
                                      load_work_others, percentile_rank)
    w = DEFAULT_WINDOW_DAYS if window_days is None else window_days
    with conn.cursor() as cur:
        cur.execute("SELECT w.title, p.avg_view_pct FROM public.clips cl "
                    "JOIN public.works w ON w.id=cl.work_id "
                    "JOIN public.clip_performance p ON p.clip_id=cl.id AND p.snapshot_window_days=%s "
                    "WHERE cl.id=%s AND p.avg_view_pct IS NOT NULL", (w, clip_id))
        r = cur.fetchone()
    if not r or r[1] is None:
        return None
    pop = [a for _, a in load_work_others(conn, r[0], comparator_exclude_db(conn, []), w)]
    return percentile_rank(float(r[1]), pop)


# 승격 신호 라우팅(순수): benchmark=증거일관 기본 · judge=빠른 프록시
SCORERS = {"benchmark": _benchmark_score, "judge": _latest_judge}


def _coverage_gate_or_exit(conn, clip_ids, window_days, force):
    """§3-6 커버리지 게이트: 두 클립의 content_id 가 laeebly 에서 +window 성숙했는지.
    ETL 은 커버리지 70%만 돼도 +7d 행을 만들므로 행 존재만으로 판정하면 조기 판정."""
    from coverage_gate import connect_laeebly, uncovered
    with conn.cursor() as cur:
        cur.execute("SELECT video_external_id FROM public.clips "
                    "WHERE id = ANY(%s) AND video_external_id IS NOT NULL", (list(clip_ids),))
        vids = [r[0] for r in cur.fetchall()]
    if not vids:
        return                                     # 미발행 → 점수도 None → 아래서 보류
    lae = connect_laeebly()
    if lae is None:
        if force:
            print("⚠ LAEEBLY_DB_URL 없음 — 커버리지 게이트 생략(--force)")
            return
        sys.exit("LAEEBLY_DB_URL 없음 — 커버리지 게이트를 못 돎(조기 판정 위험). 강행은 --force")
    try:
        bad = uncovered(lae, vids, window_days)
    finally:
        lae.close()
    if bad:
        if force:
            print(f"⚠ +{window_days}d 미성숙 {len(bad)}개 무시(--force): {bad}")
            return
        sys.exit(f"커버리지 게이트 미통과 — +{window_days}d 미성숙: {bad} (강행은 --force)")


def main():
    ap = argparse.ArgumentParser(description="실험 승격 (챔피언-챌린저 판정)")
    ap.add_argument("--experiment", required=True)
    ap.add_argument("--metric", default="benchmark", choices=["benchmark", "judge"],
                    help="benchmark=시장 대비 +7일 백분위(승격 기본·증거일관, §3-6) · judge=오프라인 빠른 프록시(성과예측 아님)")
    ap.add_argument("--margin", type=float, default=0.03)
    ap.add_argument("--window-days", type=int, default=None,
                    help="benchmark 창(기본 7 — D2). 감사 패스는 14")
    ap.add_argument("--force", action="store_true", help="커버리지 게이트 미통과/불가 시 강행")
    a = ap.parse_args()

    import psycopg
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    from m3_aivideo_benchmark import DEFAULT_WINDOW_DAYS
    from psycopg.types.json import Json
    w = DEFAULT_WINDOW_DAYS if a.window_days is None else a.window_days
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT config, metrics FROM public.experiments WHERE id=%s", (a.experiment,))
            row = cur.fetchone()
        if not row:
            sys.exit("실험 없음")
        cfg = row[0] or {}
        champ, chal = cfg.get("champion_clip"), cfg.get("challenger_clip")
        if not champ or not chal:
            sys.exit("config에 champion_clip / challenger_clip 필요")
        scorer = SCORERS[a.metric]
        if a.metric == "benchmark":
            _coverage_gate_or_exit(conn, [champ, chal], w, a.force)   # §3-6 조기판정 차단
            scored = {"champion": scorer(conn, champ, w), "challenger": scorer(conn, chal, w)}
        else:
            scored = {"champion": scorer(conn, champ), "challenger": scorer(conn, chal)}
        if a.metric == "benchmark" and any(v is None for v in scored.values()):
            print(f"experiment {a.experiment[:8]}: 벤치마크 성과 미적재(scored={scored}) — "
                  f"+{w}일 커버리지(적재지연 ~4d) 후 재실행 (판정 보류)")
            return
        winner, reason = pick_winner(scored, a.margin)
        metrics = dict(row[1] or {})
        basis = f"performance:benchmark_pctile(+{w}d)" if a.metric == "benchmark" else "offline:judge_proxy"
        metrics["decision"] = {"basis": basis, "metric": a.metric, "scored": scored,
                               "winner": winner, "reason": reason}
        status = "running" if winner == "challenger" else "retired"
        with conn.cursor() as cur:
            cur.execute("UPDATE public.experiments SET metrics=%s, status=%s, ended_at=now() WHERE id=%s",
                        (Json(metrics), status, a.experiment))
        conn.commit()
        print(f"experiment {a.experiment[:8]}: winner={winner} → status={status}")
        print(f"  {reason} | scored={scored}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
