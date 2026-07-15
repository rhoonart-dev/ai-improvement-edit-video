#!/usr/bin/env python3
"""제안기 v0 (knob_proposer) — 채점된 뱅크의 good vs bad 피처 대조로 후보 노브를 도출(계획 §2).

K1·K3 준수: **후보만** 만든다. config/프롬프트를 직접 수정하지 않고, 채택은 반드시 A/B(사람 승인).
증거 = 클러스터별 good vs bad 의 Cliff's δ(비모수 효과크기) + 표본수. 자사(origin='ours')·비활성·
mid 제외. 길이 상관 피처는 apv artifact 방어 위해 플래그.

산출: 후보 노브 랭킹 리포트(stdout/markdown). 각 후보 = {클러스터, 피처, δ, n, 방향, 조절면, 트랙}.
채택 경로: 사람 검토 → loop_controller KNOBS 등록 → 1노브/라운드 A/B → CI 게이트(K4) → 감사.

env: PIPELINE_DB_URL (fdidiqd — eb_shorts_features 뱅크)
실행: PIPELINE_DB_URL=... /Users/gimsewon/rhoonart/ai-video/.venv/bin/python scripts/knob_proposer.py
      [--min-each 15] [--min-abs-delta 0.33] [--md report.md]
"""
from __future__ import annotations

import argparse
import os

# ── 피처 → 조절면 매핑 (계획 §2-1 eb_feature_surface_map 시드) ──────────────
#    {피처: (조절면 라벨, 트랙, 노브계층)}
FEATURE_SURFACE = {
    "silence_ratio":          ("--silence-profile (무음 컷 공격성)", "cohort", "L1"),
    "speech_ratio":           ("--silence-profile (역: 발화 비율)", "cohort", "L1"),
    "duration_sec":           ("--length-profile (길이 상한)", "cohort", "L1"),
    "avg_shot_len_sec":       ("length·moment 선택 (샷 길이)", "cohort", "L1"),
    "cut_count":              ("length·moment 선택 (컷 밀도)", "cohort", "L1"),
    "cut_rhythm_var":         ("moment 선택 (컷 리듬 분산)", "cohort", "L1"),
    "loudness_dynamics":      ("게인 3종·loudness LUFS", "pair", "L1"),
    "subtitle_density":       ("자막 프리셋·max_chars/lines", "pair", "L1"),
    "video_fill_ratio":       ("레이아웃 풀블리드(박스↔풀)", "pair", "L1"),
    "hook_timing_sec":        ("훅 지시문·moment 선택", "cohort", "L1"),
    "hook_semantic_strength": ("훅 지시문 (L0 프롬프트)", "cohort", "L0"),
    "dialogue_density":       ("스토리 프롬프트 (L0)", "cohort", "L0"),
    "action_density":         ("스토리 프롬프트 (L0)", "cohort", "L0"),
}
# apv(시청유지)는 짧을수록 유리한 정규화 artifact — 길이와 얽힌 피처는 판정 시 가드레일 필수(§3-2)
LENGTH_CORRELATED = {"duration_sec", "cut_count", "avg_shot_len_sec", "subtitle_density"}
# jsonb 버킷 내 ai_features 숫자 피처 (버킷, 필드)
VLM_FEATURES = {
    "hook_semantic_strength": ("hook_0_3s", "hook_semantic_strength"),
    "dialogue_density":       ("build", "dialogue_density"),
    "action_density":         ("build", "action_density"),
}
DET_FEATURES = ["silence_ratio", "speech_ratio", "duration_sec", "avg_shot_len_sec",
                "cut_count", "cut_rhythm_var", "loudness_dynamics", "subtitle_density",
                "video_fill_ratio", "hook_timing_sec"]


# ─────────────────────────── 순수 (단위테스트) ───────────────────────────
def cliffs_delta(good, bad):
    """Cliff's δ = (#(g>b) − #(g<b)) / (n_g·n_b). 범위 [-1,1]. 표본 0이면 None.
       δ>0 = good 이 그 피처에서 더 큼."""
    if not good or not bad:
        return None
    gt = lt = 0
    for g in good:
        for b in bad:
            if g > b:
                gt += 1
            elif g < b:
                lt += 1
    return (gt - lt) / (len(good) * len(bad))


def classify_effect(delta):
    """|δ| 등급(관례): <0.147 none / <0.33 small / <0.474 medium / ≥0.474 large."""
    if delta is None:
        return "none"
    a = abs(delta)
    if a < 0.147:
        return "none"
    if a < 0.33:
        return "small"
    if a < 0.474:
        return "medium"
    return "large"


def direction_hint(feature, delta):
    """δ 부호 → '좋은 클립이 어느 방향인가' 서술."""
    hi = delta > 0
    return f"good 클립이 {feature}가 {'높음' if hi else '낮음'} (δ={delta:+.2f})"


def candidate_priority(delta, n_good, n_bad):
    """priority = |δ| × sqrt(min(n)/30, 캡 1). 강한 효과·충분한 표본이 위로."""
    if delta is None:
        return 0.0
    import math
    return abs(delta) * min(math.sqrt(min(n_good, n_bad) / 30.0), 1.0)


def contrast_cluster(rows, feature, min_each):
    """한 클러스터 rows(dict 목록) 에서 feature 의 good vs bad Cliff's δ.
       반환 (delta, n_good, n_bad) 또는 표본 미달 시 None."""
    good = [r[feature] for r in rows if r.get("perf_label") == "good" and r.get(feature) is not None]
    bad = [r[feature] for r in rows if r.get("perf_label") == "bad" and r.get(feature) is not None]
    if len(good) < min_each or len(bad) < min_each:
        return None
    return cliffs_delta(good, bad), len(good), len(bad)


def build_candidates(rows_by_cluster, min_each=15, min_abs_delta=0.33):
    """클러스터별 × 피처별 대조 → 후보 리스트(priority 내림차순).
       각 후보: {cluster, feature, delta, effect, n_good, n_bad, surface, track, knob_class,
                 length_correlated, direction, priority}."""
    features = [f for f in (DET_FEATURES + list(VLM_FEATURES)) if f in FEATURE_SURFACE]
    out = []
    for cluster, rows in rows_by_cluster.items():
        for feat in features:
            res = contrast_cluster(rows, feat, min_each)
            if not res:
                continue
            delta, ng, nb = res
            if delta is None or abs(delta) < min_abs_delta:
                continue
            surface, track, kc = FEATURE_SURFACE[feat]
            out.append({
                "cluster": cluster, "feature": feat, "delta": round(delta, 3),
                "effect": classify_effect(delta), "n_good": ng, "n_bad": nb,
                "surface": surface, "track": track, "knob_class": kc,
                "length_correlated": feat in LENGTH_CORRELATED,
                "direction": direction_hint(feat, delta),
                "priority": round(candidate_priority(delta, ng, nb), 3),
            })
    out.sort(key=lambda c: c["priority"], reverse=True)
    return out


# ─────────────────────────── I/O ───────────────────────────
def fetch_rows(conn):
    """채점된 시장 클립 + 결정론/VLM 피처. origin='ours'·mid·비활성 제외(§2-2 모집단)."""
    vlm_sel = ", ".join(
        f"({b}->'ai_features'->>'{fld}')::float AS {k}" for k, (b, fld) in VLM_FEATURES.items())
    det_sel = ", ".join(DET_FEATURES)
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT cluster_id, ip_key, perf_label, {det_sel}, {vlm_sel}
            FROM eb_shorts_features
            WHERE origin = 'market' AND perf_label IN ('good','bad')
              AND lifecycle_status = 'active' AND cluster_id IS NOT NULL
        """)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    by_cluster = {}
    for r in rows:
        by_cluster.setdefault(r["cluster_id"], []).append(r)
    return by_cluster


def render_report(cands, rows_by_cluster, min_each):
    lines = ["# 노브 후보 리포트 (제안기 v0 — good vs bad 대조)\n",
             "> K1·K3: **후보만** 제시. 채택은 사람 검토 → 1노브 A/B → CI 게이트(K4). "
             "config 직접 수정 아님.\n"]
    sizes = {c: (sum(1 for r in rs if r["perf_label"] == "good"),
                 sum(1 for r in rs if r["perf_label"] == "bad")) for c, rs in rows_by_cluster.items()}
    lines.append(f"대상 클러스터(good/bad ≥{min_each}): "
                 + ", ".join(f"{c}({g}/{b})" for c, (g, b) in sorted(sizes.items(), key=lambda x: -sum(x[1]))
                             if g >= min_each and b >= min_each) + "\n")
    if not cands:
        lines.append("\n**후보 없음** — |δ| 문턱을 넘는 피처 대조가 없음(표본 부족 또는 신호 약함).")
        return "\n".join(lines)
    lines.append("\n| # | 우선 | 클러스터 | 피처 | δ(효과) | n(g/b) | 방향 | 조절면 | 트랙 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for i, c in enumerate(cands, 1):
        warn = " ⚠길이" if c["length_correlated"] else ""
        lines.append(f"| {i} | {c['priority']} | {c['cluster']} | {c['feature']}{warn} | "
                     f"{c['delta']:+.2f}({c['effect']}) | {c['n_good']}/{c['n_bad']} | "
                     f"{c['direction'].split('(')[0].strip()} | {c['surface']} | {c['track']} |")
    lines.append("\n⚠길이 = duration 과 상관 — apv 정규화 artifact 방어 위해 판정 시 절대 시청시간 "
                 "가드레일 필수(§3-2). L0=프롬프트·L1=config. 방향은 '좋은 클립이 어느 쪽인가'.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="제안기 v0 — good vs bad 대조로 후보 노브 도출")
    ap.add_argument("--min-each", type=int, default=15, help="클러스터당 good·bad 최소(기본 15)")
    ap.add_argument("--min-abs-delta", type=float, default=0.33, help="후보 승격 |δ| 문턱(기본 0.33=medium)")
    ap.add_argument("--md", help="마크다운 리포트 저장 경로(미지정=stdout)")
    a = ap.parse_args()
    import psycopg
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    try:
        rows_by_cluster = fetch_rows(conn)
    finally:
        conn.close()
    cands = build_candidates(rows_by_cluster, a.min_each, a.min_abs_delta)
    report = render_report(cands, rows_by_cluster, a.min_each)
    if a.md:
        from pathlib import Path
        Path(a.md).write_text(report + "\n", encoding="utf-8")
        print(f"[proposer] 후보 {len(cands)}건 → {a.md}")
    else:
        print(report)


if __name__ == "__main__":
    main()
