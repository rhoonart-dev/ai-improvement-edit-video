#!/usr/bin/env python3
"""m3_aivideo_benchmark 순수 로직 테스트: percentile_rank + comparator_exclude(비교군 오염 수정).
실행: /Users/gimsewon/rhoonart/ai-video/.venv/bin/python -m pytest scripts/test_m3_benchmark.py -q"""
from m3_aivideo_benchmark import AIV_IDS, comparator_exclude, percentile_rank


def test_comparator_excludes_all_aivideo_not_just_cohort():
    # Codex #1 회귀방지: 새 코호트(AIV_IDS 와 다름)를 재도 옛 ai-video id 가 비교군에서 빠져야 함
    new_cohort = ["NEW1", "NEW2"]
    ex = comparator_exclude(new_cohort)
    assert set(AIV_IDS).issubset(set(ex))      # 옛 44개 전부 제외
    assert "NEW1" in ex and "NEW2" in ex       # 현 코호트도 제외
    assert len(ex) == len(set(ex))             # 중복 없음


def test_comparator_exclude_default_is_aiv_ids():
    assert set(comparator_exclude([])) == set(AIV_IDS)


def test_comparator_exclude_dedups_overlap():
    # 코호트가 AIV_IDS 와 겹쳐도 중복 안 생김
    ex = comparator_exclude([AIV_IDS[0], "X"])
    assert len(ex) == len(set(AIV_IDS) | {"X"})


def test_median_value():
    assert percentile_rank(3, [1, 2, 4, 5]) == 0.5      # 2개가 더 작음 / 4


def test_top_and_bottom():
    assert percentile_rank(10, [1, 2, 3]) == 1.0        # 전부보다 큼
    assert percentile_rank(0, [1, 2, 3]) == 0.0         # 전부보다 작음


def test_empty_pop_returns_none():
    assert percentile_rank(5, []) is None


def test_ties_counted_as_not_smaller():
    # 동률은 '더 작음'에 포함 안 됨 → 보수적
    assert percentile_rank(2, [2, 2, 2, 2]) == 0.0


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
