#!/usr/bin/env python3
"""⑦ golden 사람 라벨 입력 경로 + judge↔golden 캘리브레이션.

사람 앵커 라벨은 *사람이* 매겨야 한다(자동 생성 금지 — judge 보정의 기준점이므로).
이 스크립트는 입력/검증 *경로*만 제공한다. golden 라벨이 쌓이면 judge 품질을 캘리브레이션할 수 있다.

env: PIPELINE_DB_URL
실행:
  라벨 추가:    ... add_golden_label.py --clip-id <uuid> --labeler 사람A --quality 0.8 [--rubric '{"hook":0.9}']
  캘리브레이션: ... add_golden_label.py --calibrate     (judge vs golden 일치도)
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def agreement(pairs):
    """pairs: [(judge_q, human_q)] → {n, mae}. None 쌍 제외, 1쌍 미만이면 None."""
    p = [(j, h) for j, h in pairs if j is not None and h is not None]
    if not p:
        return None
    mae = sum(abs(j - h) for j, h in p) / len(p)
    return {"n": len(p), "mae": round(mae, 4)}


def main():
    ap = argparse.ArgumentParser(description="golden 사람 라벨 입력 + judge 캘리브레이션")
    ap.add_argument("--clip-id")
    ap.add_argument("--labeler")
    ap.add_argument("--quality", type=float)
    ap.add_argument("--rubric", help="7차원 등 JSON")
    ap.add_argument("--calibrate", action="store_true", help="judge_runs vs golden_human_labels 일치도")
    a = ap.parse_args()

    import psycopg
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    try:
        if a.calibrate:
            with conn.cursor() as c:
                c.execute("""select j.quality_score, g.quality_score
                             from public.judge_runs j
                             join public.golden_human_labels g on g.clip_id = j.clip_id""")
                rows = c.fetchall()
            pairs = [(float(x[0]) if x[0] is not None else None,
                      float(x[1]) if x[1] is not None else None) for x in rows]
            r = agreement(pairs)
            print("judge ↔ golden 캘리브레이션:",
                  r if r else "golden 라벨 없음 — 사람 라벨링 필요(이 스크립트로 추가)")
            return

        if not (a.clip_id and a.labeler and a.quality is not None):
            sys.exit("라벨 추가엔 --clip-id --labeler --quality 필요")
        from psycopg.types.json import Json
        rubric = json.loads(a.rubric) if a.rubric else None
        with conn.cursor() as c:
            c.execute("""insert into public.golden_human_labels(clip_id, labeler, quality_score, rubric_scores)
                         values (%s, %s, %s, %s)""",
                      (a.clip_id, a.labeler, a.quality, Json(rubric) if rubric else None))
        conn.commit()
        print(f"golden label 추가: clip {a.clip_id[:8]} labeler={a.labeler} quality={a.quality}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
