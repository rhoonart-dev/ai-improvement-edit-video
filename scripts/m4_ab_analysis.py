#!/usr/bin/env python3
"""M4 A/B 분석 — aivideo_experiments 의 treatment vs control 쌍을 성과 창으로 비교(작품내 paired).

설계: docs/AB_VALIDATION.md. directive(가설)를 채널 A/B로 인과 검증하는 마지막 단계.
같은 pair_id 안에서 treatment·control 둘 다 창 성과를 가진 쌍만 사용 → 쌍별 Δapv.
부호검정(binom) + Wilcoxon. 데이터 없으면 '아직 쌍 없음' 출력(발행·성과수집 대기).

§3-6: 판정 창 기본 +7d (감사는 --window-days 14).
§4-3 R5: |published_at(T)−published_at(C)| > 48h 인 쌍은 분석에서 제외(몰래 before/after 차단).
§4-2 가드레일: apv 는 짧을수록 유리한 정규화 artifact → 절대 views·likes·shares Δ를 자동 병기.
   특히 길이 노브 실험은 가드레일 없이 판정 금지.

실행: PIPELINE_DB_URL=... /Users/gimsewon/rhoonart/ai-video/.venv/bin/python \
        scripts/m4_ab_analysis.py --experiment loudness_v1
의존: psycopg scipy
"""
import argparse
import os
from datetime import timedelta

import psycopg
from scipy.stats import binomtest, wilcoxon

DEFAULT_WINDOW_DAYS = 7        # §3-6 (m3 와 동일 리듬)
R5_MAX_GAP_HOURS = 48          # §4-3 발행시각 근접 불변식
GUARDRAIL_METRICS = ["views", "likes", "shares"]   # §4-2 절대 지표(정규화 artifact 방어)


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


def pairs_within_gap(by_pair, max_hours=R5_MAX_GAP_HOURS):
    """R5(§4-3): 발행시각이 max_hours 초과로 벌어진(또는 미상인) 쌍 제외.
    by_pair: {pair_id: {arm: (metric, published_at|None)}}. → (pairs, dropped_pair_ids)."""
    pairs, dropped = [], []
    for pid, d in by_pair.items():
        if "treatment" not in d or "control" not in d:
            continue
        (tm, tp), (cm, cp) = d["treatment"], d["control"]
        if tp is None or cp is None or abs(tp - cp) > timedelta(hours=max_hours):
            dropped.append(pid)
            continue
        pairs.append((tm, cm))
    return pairs, dropped


# ---------- I/O ----------
def _gate_or_exit(conn, experiment_key, window_days, force):
    """§3-6 커버리지 게이트: 실험의 전 arm content_id 가 laeebly 에서 +window 성숙했는지."""
    import sys

    from coverage_gate import connect_laeebly, uncovered
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT video_external_id FROM aivideo_experiments "
                    "WHERE experiment_key = %s", (experiment_key,))
        vids = [r[0] for r in cur.fetchall() if r[0]]
    if not vids:
        return
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
            print(f"⚠ +{window_days}d 미성숙 {len(bad)}개 무시(--force): {bad[:5]}")
            return
        sys.exit(f"커버리지 게이트 미통과 — +{window_days}d 미성숙 {len(bad)}/{len(vids)}개: "
                 f"{bad[:5]}{'…' if len(bad) > 5 else ''} (강행은 --force)")


def load_pairs(conn, experiment_key, metric, window_days=DEFAULT_WINDOW_DAYS):
    cur = conn.cursor()
    cur.execute(f"""
        SELECT e.pair_id, e.arm, p.{metric} AS m, c.published_at
        FROM aivideo_experiments e
        JOIN clips c ON c.video_external_id = e.video_external_id
        JOIN clip_performance p ON p.clip_id = c.id AND p.snapshot_window_days = %s
        WHERE e.experiment_key = %s AND p.{metric} IS NOT NULL
    """, (window_days, experiment_key))
    by_pair = {}
    for pair_id, arm, m, pub in cur.fetchall():
        by_pair.setdefault(pair_id, {})[arm] = (float(m), pub)
    pairs, dropped = pairs_within_gap(by_pair)
    if dropped:
        print(f"⚠ R5 위반/발행시각 미상으로 제외된 쌍 {len(dropped)}개: {dropped[:5]} "
              f"(|Δpublished_at| > {R5_MAX_GAP_HOURS}h 또는 published_at NULL)")
    return pairs


def _load_watch_time_pairs(conn, experiment_key, window_days):
    """절대 시청시간(추정) 쌍 — clip_performance 에 watch_time 컬럼이 없어
    avg_view_pct × duration_sec × views(=총 시청초 추정)로 계산. §4-2 의 1순위 가드레일."""
    cur = conn.cursor()
    cur.execute("""
        SELECT e.pair_id, e.arm,
               p.avg_view_pct * c.duration_sec * p.views AS m, c.published_at
        FROM aivideo_experiments e
        JOIN clips c ON c.video_external_id = e.video_external_id
        JOIN clip_performance p ON p.clip_id = c.id AND p.snapshot_window_days = %s
        WHERE e.experiment_key = %s
          AND p.avg_view_pct IS NOT NULL AND c.duration_sec IS NOT NULL AND p.views IS NOT NULL
    """, (window_days, experiment_key))
    by_pair = {}
    for pair_id, arm, m, pub in cur.fetchall():
        by_pair.setdefault(pair_id, {})[arm] = (float(m), pub)
    return pairs_within_gap(by_pair)[0]


def guardrails(conn, experiment_key, window_days=DEFAULT_WINDOW_DAYS):
    """§4-2 가드레일 자동화: 절대 지표의 쌍별 Δ 요약.
    apv 는 길이 정규화 artifact 라 절대 지표 역행 여부를 병기 — 길이 노브는 이것 없이 판정 금지.
    watch_sec_est = avg_view_pct×duration×views (절대 시청시간 추정 — 1순위 가드레일).
    likes/shares 는 root ETL 이 아직 미적재라 보통 None(표시로 침묵 방지)."""
    out = {}
    wt = _load_watch_time_pairs(conn, experiment_key, window_days)
    if wt:
        deltas = [t - c for t, c in wt]
        out["watch_sec_est"] = {"n": len(deltas), "mean_delta": sum(deltas) / len(deltas),
                                "wins": sum(1 for d in deltas if d > 0)}
    else:
        out["watch_sec_est"] = None
    for m in GUARDRAIL_METRICS:
        pairs = load_pairs(conn, experiment_key, m, window_days)
        if not pairs:
            out[m] = None
            continue
        deltas = [t - c for t, c in pairs]
        out[m] = {"n": len(deltas), "mean_delta": sum(deltas) / len(deltas),
                  "wins": sum(1 for d in deltas if d > 0)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", required=True, help="experiment_key (예: loudness_v1)")
    ap.add_argument("--metric", default="avg_view_pct", help="avg_view_pct(주) | views(부)")
    ap.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS,
                    help=f"성과 창(기본 {DEFAULT_WINDOW_DAYS} — §3-6). 감사 패스는 14")
    ap.add_argument("--force", action="store_true", help="커버리지 게이트 미통과/불가 시 강행")
    a = ap.parse_args()
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    _gate_or_exit(conn, a.experiment, a.window_days, a.force)   # §3-6 조기판정 차단
    pairs = load_pairs(conn, a.experiment, a.metric, a.window_days)
    s = paired_stats(pairs)
    print(f"experiment={a.experiment} metric={a.metric} window=+{a.window_days}d")
    if s["n_pairs"] == 0:
        print(f"아직 완성된 treatment/control 쌍이 +{a.window_days}일 성과와 함께 없음 — 발행·성과수집 대기.")
        print("(체크: aivideo_experiments 에 같은 pair_id 의 두 arm 이 기록되고, "
              f"각 video_external_id 가 clips/clip_performance(+{a.window_days}d)에 적재됐는가.)")
        return
    print(f"쌍={s['n_pairs']}  treatment 승={s['treatment_wins']}  "
          f"mean Δ{a.metric}={s['mean_delta_apv']:+.3f}  "
          f"sign_p≥={s['sign_p_greater']:.3f}  wilcoxon_p≥={s['wilcoxon_p_greater']}")
    # §4-2 가드레일: 절대 지표 병기 — apv 개선이 '짧아진 것' 만의 artifact 인지 즉시 보이게
    print("가드레일(절대 지표, §4-2):")
    g_all = guardrails(conn, a.experiment, a.window_days)
    for m, g in g_all.items():
        if g is None:
            print(f"  {m:13s}: (데이터 없음 — ETL 미적재)")
        else:
            print(f"  {m:13s}: mean Δ={g['mean_delta']:+.1f}  treatment 승 {g['wins']}/{g['n']}")
    win = (s["mean_delta_apv"] or 0) > 0 and s["sign_p_greater"] < 0.05
    print("결정:", "✅ treatment(directive) 인과 채택 — 가드레일 역행 없는지 위 절대 지표 확인" if win
          else "❌ 미검증(효과 0 또는 유의하지 않음) — 기각/추가표본")
    if win and g_all.get("watch_sec_est") is None:
        print("⚠ §4-2: 절대 시청시간 가드레일 데이터 없음 — 길이 노브 실험이면 이 결과로 판정 금지"
              "(apv 는 짧을수록 유리한 정규화 artifact).")


if __name__ == "__main__":
    main()
