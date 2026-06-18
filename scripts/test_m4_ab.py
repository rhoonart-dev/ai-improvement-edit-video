#!/usr/bin/env python3
"""m4_ab_analysis 순수 로직 테스트: paired_stats (A/B 쌍별 Δ 부호검정·Wilcoxon·결정).
실행: /Users/gimsewon/rhoonart/ai-video/.venv/bin/python -m pytest scripts/test_m4_ab.py -q"""
from m4_ab_analysis import paired_stats


def test_empty():
    s = paired_stats([])
    assert s["n_pairs"] == 0
    assert s["mean_delta_apv"] is None
    assert s["sign_p_greater"] == 1.0
    assert s["wilcoxon_p_greater"] is None


def test_all_treatment_wins_significant():
    # treatment 가 control 보다 일관되게 높음(8쌍 전부) → mean Δ>0, sign_p 작음
    pairs = [(0.5, 0.3), (0.6, 0.4), (0.55, 0.35), (0.7, 0.5),
             (0.52, 0.4), (0.61, 0.45), (0.58, 0.5), (0.49, 0.4)]
    s = paired_stats(pairs)
    assert s["n_pairs"] == 8
    assert s["treatment_wins"] == 8
    assert s["mean_delta_apv"] > 0
    assert s["sign_p_greater"] < 0.05
    assert s["wilcoxon_p_greater"] is not None and s["wilcoxon_p_greater"] < 0.05


def test_no_effect_not_significant():
    # 절반씩 → 효과 없음
    pairs = [(0.5, 0.4), (0.4, 0.5), (0.6, 0.5), (0.5, 0.6)]
    s = paired_stats(pairs)
    assert s["treatment_wins"] == 2
    assert s["sign_p_greater"] > 0.05


def test_wilcoxon_skipped_small_n():
    # n<6 이면 wilcoxon None (부호검정만)
    s = paired_stats([(0.5, 0.3), (0.6, 0.4), (0.55, 0.35)])
    assert s["n_pairs"] == 3
    assert s["wilcoxon_p_greater"] is None


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
