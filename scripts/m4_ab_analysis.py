#!/usr/bin/env python3
"""M4 A/B 분석 — aivideo_experiments 의 treatment vs control 쌍을 +14일 성과로 비교(작품내 paired).

설계: docs/AB_VALIDATION.md. directive(가설)를 채널 A/B로 인과 검증하는 마지막 단계.
같은 pair_id 안에서 treatment·control 둘 다 +14일 성과를 가진 쌍만 사용 → 쌍별 Δapv.
부호검정(binom) + Wilcoxon. 데이터 없으면 '아직 쌍 없음' 출력(발행·성과수집 대기).

실행: PIPELINE_DB_URL=... /Users/gimsewon/rhoonart/ai-video/.venv/bin/python \
        scripts/m4_ab_analysis.py --experiment loudness_v1
의존: psycopg scipy
"""
import argparse
import os

import psycopg
from scipy.stats import binomtest, wilcoxon


# ---------- 순수 로직(테스트 대상) ----------
def paired_stats(pairs):
    """pairs: [(treatment_apv, control_apv), ...]. 쌍별 Δ=treat−ctrl 의 부호검정+Wilcoxon.

    결정규칙(AB_VALIDATION §6): mean Δ>0 & p<0.05 → treatment(=directive) 인과 채택.
    """
    deltas = [t - c for t, c in pairs]
    n = len(deltas)
    if n == 0:
        return {"n_pairs": 0, "treatment_wins": 0, "mean_delta_apv": None,
                "sign_p_greater": 1.0, "wilcoxon_p_greater": None}
    wins = sum(1 for d in deltas if d > 0)
    mean_delta = sum(deltas) / n
    sign_p = binomtest(wins, n, 0.5, alternative="greater").pvalue
    wil_p = None
    if n >= 6 and any(d != 0 for d in deltas):
        wil_p = float(wilcoxon(deltas, alternative="greater").pvalue)
    return {"n_pairs": n, "treatment_wins": wins, "mean_delta_apv": mean_delta,
            "sign_p_greater": float(sign_p), "wilcoxon_p_greater": wil_p}


# ---------- I/O ----------
def load_pairs(conn, experiment_key, metric):
    cur = conn.cursor()
    cur.execute(f"""
        SELECT e.pair_id, e.arm, p.{metric} AS m
        FROM aivideo_experiments e
        JOIN clips c ON c.video_external_id = e.video_external_id
        JOIN clip_performance p ON p.clip_id = c.id AND p.snapshot_window_days = 14
        WHERE e.experiment_key = %s AND p.{metric} IS NOT NULL
    """, (experiment_key,))
    by_pair = {}
    for pair_id, arm, m in cur.fetchall():
        by_pair.setdefault(pair_id, {})[arm] = float(m)
    pairs = [(d["treatment"], d["control"]) for d in by_pair.values()
             if "treatment" in d and "control" in d]
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", required=True, help="experiment_key (예: loudness_v1)")
    ap.add_argument("--metric", default="avg_view_pct", help="avg_view_pct(주) | views(부)")
    a = ap.parse_args()
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    pairs = load_pairs(conn, a.experiment, a.metric)
    s = paired_stats(pairs)
    print(f"experiment={a.experiment} metric={a.metric}")
    if s["n_pairs"] == 0:
        print("아직 완성된 treatment/control 쌍이 +14일 성과와 함께 없음 — 발행·성과수집 대기.")
        print("(체크: aivideo_experiments 에 같은 pair_id 의 두 arm 이 기록되고, "
              "각 video_external_id 가 clips/clip_performance(+14d)에 적재됐는가.)")
        return
    print(f"쌍={s['n_pairs']}  treatment 승={s['treatment_wins']}  "
          f"mean Δ{a.metric}={s['mean_delta_apv']:+.3f}  "
          f"sign_p≥={s['sign_p_greater']:.3f}  wilcoxon_p≥={s['wilcoxon_p_greater']}")
    win = (s["mean_delta_apv"] or 0) > 0 and s["sign_p_greater"] < 0.05
    print("결정:", "✅ treatment(directive) 인과 채택" if win
          else "❌ 미검증(효과 0 또는 유의하지 않음) — 기각/추가표본")


if __name__ == "__main__":
    main()
