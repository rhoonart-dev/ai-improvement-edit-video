#!/usr/bin/env python3
"""⑤ 실험 승격 — 챔피언-챌린저를 오프라인 신호(judge)로 판정 → experiments 갱신.

챌린저가 마진 넘게 이기면 status='running'(온라인 검증 대기), 못 이기면 'retired'(오프라인 기각).
발행 후 실측(retention)이 들어오면 동일 메커니즘으로 재판정한다.

env: PIPELINE_DB_URL
실행:
  ... python scripts/decide_experiment.py --experiment <uuid> [--metric judge] [--margin 0.03]
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
    with conn.cursor() as cur:
        cur.execute("SELECT quality_score FROM public.judge_runs WHERE clip_id=%s "
                    "ORDER BY created_at DESC LIMIT 1", (clip_id,))
        r = cur.fetchone()
    return float(r[0]) if r and r[0] is not None else None


def main():
    ap = argparse.ArgumentParser(description="실험 승격 (챔피언-챌린저 판정)")
    ap.add_argument("--experiment", required=True)
    ap.add_argument("--metric", default="judge", choices=["judge"])
    ap.add_argument("--margin", type=float, default=0.03)
    a = ap.parse_args()

    import psycopg
    from psycopg.types.json import Json
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
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
        scored = {"champion": _latest_judge(conn, champ), "challenger": _latest_judge(conn, chal)}
        winner, reason = pick_winner(scored, a.margin)
        metrics = dict(row[1] or {})
        metrics["decision"] = {"basis": f"offline:{a.metric}", "scored": scored,
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
