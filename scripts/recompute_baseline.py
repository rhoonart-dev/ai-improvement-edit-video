#!/usr/bin/env python3
"""§3-6 baseline 재산출 — loop_state 의 baseline 을 +7d 창 기준으로 1회 재계산.

기존 baseline 0.21 은 (a) +14d apv 기준이고 (b) m3 의 '길이매칭(±20%)' 산식이라,
라운드 측정(cohort_percentile = raw 풀 백분위)과 창·산식이 둘 다 어긋난다.
여기서는 **측정과 동일한 산식(cohort_percentile, raw)** 으로 구 코호트(AIV_IDS 레거시 44)를
+7d 창에서 재계산해 기준선을 맞춘다 — 안 하면 첫 라운드부터 기준선 불일치(§3-6).

env: PIPELINE_DB_URL
실행:
  dry :  ... python scripts/recompute_baseline.py
  반영:  ... python scripts/recompute_baseline.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_controller import AUDIT_WINDOW_DAYS, STATE_PATH, cohort_percentile, load_state  # noqa: E402
from m3_aivideo_benchmark import AIV_IDS, DEFAULT_WINDOW_DAYS  # noqa: E402


# ─────────────────────────── 순수 (단위테스트) ───────────────────────────
def apply_baseline(state: dict, pct: float, window_days: int, n: int,
                   audit_pct=None, audit_window_days=None, audit_n=None) -> dict:
    """state 에 새 baseline 기록 + 이전 값은 baseline_history 로 보존(감사 추적).
       감사 창(+14d) 기준선도 함께 기록 — cmd_audit 이 교차-창 비교를 하지 않도록."""
    hist = list(state.get("baseline_history") or [])
    hist.append({"pct": state.get("baseline_pct"),
                 "window_days": state.get("baseline_window_days") or 14,
                 "note": "replaced by recompute_baseline"})
    return {**state, "baseline_pct": pct, "baseline_window_days": window_days,
            "baseline_n": n, "baseline_method": "cohort_percentile:raw",
            "audit_baseline_pct": audit_pct, "audit_baseline_window_days": audit_window_days,
            "audit_baseline_n": audit_n,
            "baseline_history": hist}


# ─────────────────────────── I/O ───────────────────────────
def main():
    ap = argparse.ArgumentParser(description="baseline 재산출(측정과 동일 산식) — 판정 창 + 감사 창 동시")
    ap.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    ap.add_argument("--apply", action="store_true", help="loop_state.json 갱신(기본 dry-run)")
    a = ap.parse_args()

    import psycopg
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    pct, n = cohort_percentile(conn, AIV_IDS, a.window_days)
    if pct is None:
        sys.exit(f"+{a.window_days}d 성과 매칭 0건 — ETL 적재 확인 필요")
    audit_pct, audit_n = cohort_percentile(conn, AIV_IDS, AUDIT_WINDOW_DAYS)
    s = load_state()
    old_w = s.get("baseline_window_days") or 14
    print(f"구 baseline: {s['baseline_pct']*100:.0f}%ile (+{old_w}d, 길이매칭 산식) → "
          f"신 baseline: {pct*100:.0f}%ile (+{a.window_days}d, raw n={n})")
    if audit_pct is not None:
        print(f"감사 기준선(+{AUDIT_WINDOW_DAYS}d, raw): {audit_pct*100:.0f}%ile (n={audit_n})")
    else:
        print(f"⚠ 감사 기준선(+{AUDIT_WINDOW_DAYS}d) 산출 실패 — audit 은 재실행 전까지 불가")
    print("⚠ 산식 차이 주의: 구 값은 m3 길이매칭 백분위, 신 값은 측정(cohort_percentile)과 동일한 raw 풀 백분위")
    if not a.apply:
        print("[dry-run] 반영은 --apply")
        return
    ns = apply_baseline(s, pct, a.window_days, n,
                        audit_pct=audit_pct,
                        audit_window_days=AUDIT_WINDOW_DAYS if audit_pct is not None else None,
                        audit_n=audit_n)
    ns["baseline_recomputed_at"] = datetime.now(timezone.utc).isoformat()
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(ns, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[apply] loop_state.json 갱신 완료 — 이후 measure 는 +{a.window_days}d 창으로 판정 가능")


if __name__ == "__main__":
    main()
