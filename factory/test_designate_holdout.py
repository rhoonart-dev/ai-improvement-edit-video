#!/usr/bin/env python
"""§3-7 홀드아웃 선정 순수 로직 테스트."""
from config import HOLDOUT_WORKS, INJECTION_HOLDOUT_CLUSTERS
from designate_holdout import pick_holdout_clusters


def test_pick_requires_good_and_bad_min():
    stats = [
        {"cluster_id": "A", "good": 20, "bad": 20},   # 자격 O
        {"cluster_id": "B", "good": 30, "bad": 5},    # bad 부족
        {"cluster_id": "C", "good": 5, "bad": 30},    # good 부족
    ]
    assert pick_holdout_clusters(stats, k=2, min_each=15) == ["A"]


def test_pick_prefers_smaller_sample():
    """자격 있는 것 중 표본 작은 순 — 큰 클러스터 통째 홀드아웃 = 주입 손실 최소화."""
    stats = [
        {"cluster_id": "big", "good": 100, "bad": 100},
        {"cluster_id": "small", "good": 16, "bad": 16},
        {"cluster_id": "mid", "good": 40, "bad": 40},
    ]
    assert pick_holdout_clusters(stats, k=2, min_each=15) == ["small", "mid"]


def test_pick_empty_when_none_qualify():
    stats = [{"cluster_id": "A", "good": 0, "bad": 0}]
    assert pick_holdout_clusters(stats, min_each=15) == []


def test_holdout_config_defaults_empty():
    """미채점 상태에선 홀드아웃이 비어 있어야(메커니즘만 존재, 지정은 채점 후)."""
    assert INJECTION_HOLDOUT_CLUSTERS == frozenset()
    assert HOLDOUT_WORKS == frozenset()
