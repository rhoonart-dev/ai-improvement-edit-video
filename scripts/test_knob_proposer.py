#!/usr/bin/env python3
"""제안기(knob_proposer) 순수 로직 테스트 — 증거→노브 후보 도출(계획 §2).
실행: /Users/gimsewon/rhoonart/ai-video/.venv/bin/python -m pytest scripts/test_knob_proposer.py -q"""
from __future__ import annotations


def test_cliffs_delta_sign_and_range():
    """δ = P(good>bad) − P(good<bad). good 이 크면 +, 작으면 −, 완전분리면 ±1."""
    from knob_proposer import cliffs_delta
    assert cliffs_delta([3, 4, 5], [0, 1, 2]) == 1.0        # good 전부 큼
    assert cliffs_delta([0, 1, 2], [3, 4, 5]) == -1.0       # good 전부 작음
    assert abs(cliffs_delta([1, 2, 3], [1, 2, 3])) < 1e-9   # 동일분포 → 0
    assert cliffs_delta([], [1, 2]) is None                 # 표본 부족


def test_cliffs_delta_partial():
    from knob_proposer import cliffs_delta
    # good=[2,4], bad=[1,3]: 쌍 (2,1)+,(2,3)-,(4,1)+,(4,3)+ → (3-1)/4 = 0.5
    assert abs(cliffs_delta([2, 4], [1, 3]) - 0.5) < 1e-9


def test_classify_effect_thresholds():
    """|δ| 등급: <0.147 무시 / 0.147 small / 0.33 medium / 0.474 large (관례)."""
    from knob_proposer import classify_effect
    assert classify_effect(0.05) == "none"
    assert classify_effect(0.20) == "small"
    assert classify_effect(0.40) == "medium"
    assert classify_effect(-0.50) == "large"    # 부호 무관 크기


def test_feature_surface_map_covers_core_features():
    """핵심 피처가 조절면에 매핑돼 있어야 후보로 승격 가능(§2-1)."""
    from knob_proposer import FEATURE_SURFACE
    for f in ("silence_ratio", "duration_sec", "loudness_dynamics",
              "subtitle_density", "video_fill_ratio", "hook_semantic_strength"):
        assert f in FEATURE_SURFACE
        surface, track, knob_class = FEATURE_SURFACE[f]
        assert track in ("cohort", "pair")
        assert knob_class in ("L0", "L1", "L2")


def test_direction_hint():
    """δ 부호 → '좋은 클립 방향' 서술. 무음은 낮을수록(good δ<0) → aggressive 컷."""
    from knob_proposer import direction_hint
    assert "낮" in direction_hint("silence_ratio", -0.4)   # good 이 무음 적음
    assert "높" in direction_hint("silence_ratio", 0.4)    # good 이 무음 많음


def test_candidate_priority_ranks_stronger_evidence_first():
    """priority = |δ| × sqrt(min(ng,nb)/30 캡1). 강한 δ·큰 표본이 위로."""
    from knob_proposer import candidate_priority
    strong = candidate_priority(delta=0.5, n_good=30, n_bad=30)
    weak = candidate_priority(delta=0.2, n_good=30, n_bad=30)
    small_n = candidate_priority(delta=0.5, n_good=5, n_bad=5)
    assert strong > weak
    assert strong > small_n                                 # 표본 작으면 감쇠


if __name__ == "__main__":
    import sys

    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
