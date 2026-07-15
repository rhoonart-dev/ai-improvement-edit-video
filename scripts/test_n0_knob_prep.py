#!/usr/bin/env python3
"""N0 노브 준비 — §3-1 CI 게이트 · §3-2 코호트 가드레일 · §3-3 R6 순수 로직 테스트.
실행: /Users/gimsewon/rhoonart/ai-video/.venv/bin/python -m pytest scripts/test_n0_knob_prep.py -q"""
from __future__ import annotations


# ─────────────────────────── §3-1 CI 게이트 (K4) ───────────────────────────
def test_cluster_bootstrap_ci_needs_two_works():
    """작품 단위 부트스트랩: 작품 1개면 교차-작품 분산을 못 재 CI 불가(하한=None)."""
    from loop_controller import cluster_bootstrap_ci
    lo, hi, pt = cluster_bootstrap_ci({"W1": [0.4, 0.5, 0.6]}, n_boot=200, seed=0)
    assert lo is None and hi is None
    assert abs(pt - 0.5) < 1e-9              # 점추정은 풀 평균


def test_cluster_bootstrap_ci_brackets_point():
    from loop_controller import cluster_bootstrap_ci
    wr = {"W1": [0.7, 0.8], "W2": [0.6, 0.75], "W3": [0.65, 0.72], "W4": [0.68, 0.71]}
    lo, hi, pt = cluster_bootstrap_ci(wr, n_boot=1000, alpha=0.10, seed=7)
    assert lo is not None and lo <= pt <= hi
    assert 0.0 <= lo <= hi <= 1.0


def test_cluster_bootstrap_ci_deterministic_with_seed():
    from loop_controller import cluster_bootstrap_ci
    wr = {"A": [0.3, 0.4], "B": [0.5, 0.6], "C": [0.2, 0.35]}
    r1 = cluster_bootstrap_ci(wr, n_boot=500, seed=42)
    r2 = cluster_bootstrap_ci(wr, n_boot=500, seed=42)
    assert r1 == r2


def test_cluster_bootstrap_ci_empty():
    from loop_controller import cluster_bootstrap_ci
    assert cluster_bootstrap_ci({}, n_boot=100, seed=0) == (None, None, None)


def test_judge_cohort_ci_requires_both_gates():
    """채택 = (CI 하한 > baseline) AND (점추정 − baseline > margin)."""
    from loop_controller import judge_cohort_ci
    base, m = 0.21, 0.03
    # 둘 다 통과
    assert judge_cohort_ci(0.40, 0.26, base, m) == "adopt"
    # margin 통과하나 CI 하한 ≤ baseline (노이즈 문턱 미달)
    assert judge_cohort_ci(0.40, 0.19, base, m) == "reject"
    # CI 하한은 넘으나 점추정 margin 미달 (0.235−0.21=0.025 ≤ 0.03)
    assert judge_cohort_ci(0.235, 0.22, base, m) == "reject"
    # 점추정 None
    assert judge_cohort_ci(None, None, base, m) is None


def test_judge_cohort_ci_no_ci_cannot_adopt():
    """CI 불가(작품<2)면 노이즈 문턱 확인 불가 → 채택 불가(보수적)."""
    from loop_controller import judge_cohort_ci
    assert judge_cohort_ci(0.9, None, 0.21, 0.03) == "hold_no_ci"


def test_round_verdict_label_surfaces_artifact_warn():
    """리뷰 확정(major): status 요약 라벨이 artifact_warn 을 '✅채택권' 으로 숨기면 안 됨."""
    from loop_controller import round_verdict_label
    base = 0.21
    # CI·margin 통과했으나 가드레일 artifact_warn → 채택권 아님(보류 표기)
    warn = {"pct": 0.62, "ci_lo": 0.30, "guardrail": "artifact_warn"}
    assert "채택권" not in round_verdict_label(warn, base)
    assert "artifact" in round_verdict_label(warn, base)
    # 가드레일 ok → 정상 채택권
    ok = {"pct": 0.40, "ci_lo": 0.26, "guardrail": "ok"}
    assert round_verdict_label(ok, base) == "✅채택권"
    # blocked → 보류
    blk = {"pct": 0.5, "ci_lo": 0.3, "guardrail": "blocked"}
    assert "보류" in round_verdict_label(blk, base)
    # 미채택
    rej = {"pct": 0.22, "ci_lo": 0.15, "guardrail": "ok"}
    assert round_verdict_label(rej, base) == "❌미채택"


def test_r6_check_independent_of_allow_unverified():
    """리뷰 확정(major): R6 이중소속 검사가 --allow-unverified 블록 밖에 있어야 함
       (provenance 우회가 무관한 안전 불변식까지 끄면 안 됨) — 소스 구조 회귀 방지."""
    import inspect

    from loop_controller import cmd_record
    src = inspect.getsource(cmd_record)
    # R6 호출(experiment_member_ids)이 provenance 우회 가드보다 앞(밖)에 나와야 한다
    i_r6 = src.find("experiment_member_ids")
    i_guard = src.find("if not args.allow_unverified")
    assert i_r6 != -1 and i_guard != -1
    assert i_r6 < i_guard, "R6 검사가 여전히 --allow-unverified 블록 안에 있음"


# ─────────────────────────── §3-2 코호트 가드레일 ───────────────────────────
def test_guardrail_verdict_length_knob_blocked_without_watchsec():
    """길이 영향 노브(length·silence)인데 절대 시청시간 백분위가 없으면 판정 금지."""
    from loop_controller import BASELINE_CONFIG, guardrail_verdict
    cfg = {**BASELINE_CONFIG, "length": "tight"}
    assert guardrail_verdict(cfg, BASELINE_CONFIG, apv_pct=0.5, ws_pct=None) == "blocked"


def test_guardrail_verdict_artifact_warn():
    """apv 백분위가 절대 시청시간 백분위보다 크게 높으면 '짧아서 얻은' 이득 의심."""
    from loop_controller import BASELINE_CONFIG, guardrail_verdict
    cfg = {**BASELINE_CONFIG, "silence": "aggressive"}
    assert guardrail_verdict(cfg, BASELINE_CONFIG, apv_pct=0.60, ws_pct=0.30, gap=0.15) == "artifact_warn"
    assert guardrail_verdict(cfg, BASELINE_CONFIG, apv_pct=0.40, ws_pct=0.36, gap=0.15) == "ok"


def test_guardrail_verdict_non_length_knob_ok():
    """길이와 무관한 노브(loudness)는 절대 시청시간 없어도 가드레일 통과."""
    from loop_controller import BASELINE_CONFIG, guardrail_verdict
    cfg = {**BASELINE_CONFIG, "loudness": "-14"}
    assert guardrail_verdict(cfg, BASELINE_CONFIG, apv_pct=0.5, ws_pct=None) == "ok"


def test_guardrail_verdict_no_baseline_treats_as_length():
    """base_config 미상이면 보수적으로 길이 영향으로 간주(절대지표 요구)."""
    from loop_controller import guardrail_verdict
    cfg = {"silence": "aggressive", "length": "tight", "loudness": "-14"}
    assert guardrail_verdict(cfg, None, apv_pct=0.5, ws_pct=None) == "blocked"


# ─────────────────────────── §3-3 R6 (실험 소속 유일성) ───────────────────────────
def test_membership_overlap():
    from loop_controller import membership_overlap
    assert membership_overlap(["A", "B", "C"], {"B"}) == ["B"]
    assert membership_overlap(["A", "B"], set()) == []
    assert membership_overlap(["A", "B", "A"], {"A"}) == ["A", "A"]   # 순서·중복 보존


def test_loop_cohort_ids_reads_all_rounds(tmp_path):
    import json

    from register_ab_experiment import loop_cohort_ids
    state = {"rounds": [
        {"round": 1, "cohort_ids": ["X1", "X2"]},
        {"round": 2, "cohort_ids": None},
        {"round": 3, "cohort_ids": ["X3"]},
    ]}
    p = tmp_path / "loop_state.json"
    p.write_text(json.dumps(state), encoding="utf-8")
    assert loop_cohort_ids(p) == {"X1", "X2", "X3"}
    assert loop_cohort_ids(tmp_path / "missing.json") == set()


# ─────────────────────────── §3-8 link_published snippet 파라미터 ───────────────────────────
def test_link_published_accepts_snippet_kwarg():
    """link_published 가 snippet 키워드를 받아들이는가(§3-8) — 시그니처 계약."""
    import inspect

    from link_published import link_published
    assert "snippet" in inspect.signature(link_published).parameters


def test_publish_youtube_builds_snippet_with_channel_privacy():
    """발행 스니펫에 채널·공개범위가 붙는지(build_snippet 재사용 + 메타)."""
    import publish_youtube as pub
    snip = pub.build_snippet("제목", ["#로맨스"])
    enriched = {**snip, "channel": "이불 속 극장", "privacy": "unlisted"}
    assert enriched["title"] == "제목" and enriched["channel"] == "이불 속 극장"
    assert enriched["tags"] == ["로맨스"]


if __name__ == "__main__":
    import sys

    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
