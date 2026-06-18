#!/usr/bin/env python3
"""m2_pairwise_judge 순수 로직 테스트: 양방향 스왑 일관성 해석 + 일치도 집계.
실행: /Users/gimsewon/rhoonart/ai-video/.venv/bin/python -m pytest scripts/test_m2_pairwise.py -q"""
from m2_pairwise_judge import resolve_winner, summarize


def test_resolve_consistent_hi():
    # hi를 A로 보여줬을 때 A(=hi) 선택, lo를 A로 보여줬을 때 B(=hi) 선택 → 일관되게 hi
    assert resolve_winner({"winner": "A"}, {"winner": "B"}) == "hi"


def test_resolve_consistent_lo():
    # hi를 A로 보여줬을 때 B(=lo), lo를 A로 보여줬을 때 A(=lo) → 일관되게 lo
    assert resolve_winner({"winner": "B"}, {"winner": "A"}) == "lo"


def test_resolve_inconsistent_position_bias():
    # 두 순서 모두 'A'만 고름 = 위치(A)편향 → 비일관(None)
    assert resolve_winner({"winner": "A"}, {"winner": "A"}) is None
    # 두 순서 모두 'B'만 고름 → 비일관(None)
    assert resolve_winner({"winner": "B"}, {"winner": "B"}) is None


def test_summarize_counts_and_agreement():
    recs = [{"winner": "hi"}, {"winner": "hi"}, {"winner": "hi"},
            {"winner": "lo"}, {"winner": None}, {"error": "x", "winner": None}]
    s = summarize(recs)
    assert s["pairs_total"] == 6
    assert s["consistent"] == 4          # None 2개 제외
    assert s["inconsistent"] == 2
    assert s["agree_hi"] == 3
    assert abs(s["agreement"] - 0.75) < 1e-9
    assert 0.0 <= s["binom_p_greater"] <= 1.0


def test_summarize_empty():
    s = summarize([{"winner": None}])
    assert s["consistent"] == 0
    assert s["agreement"] is None
    assert s["binom_p_greater"] == 1.0


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
