#!/usr/bin/env python3
"""loop_controller 순수 정책 테스트 — coordinate-ascent 결정 로직.
실행: /Users/gimsewon/rhoonart/ai-video/.venv/bin/python -m pytest scripts/test_loop_controller.py -q"""
from loop_controller import (
    ALL_ON,
    best_round,
    config_to_flags,
    neighbors,
    next_config,
    partition_provenance,
)

# ALL_ON 의 3 이웃(1노브 토글)
NB_SIL = {"silence": "conservative", "length": "tight", "loudness": "-14"}
NB_LEN = {"silence": "aggressive", "length": "standard", "loudness": "-14"}
NB_LOUD = {"silence": "aggressive", "length": "tight", "loudness": "off"}


def test_first_proposal_is_all_on():
    assert next_config([]) == ALL_ON


def test_waits_when_all_on_proposed_but_unmeasured():
    assert next_config([{"config": ALL_ON, "pct": None}]) is None


def test_neighbors_are_single_knob_toggles():
    nb = neighbors(ALL_ON)
    assert len(nb) == 3
    for c in nb:
        diff = sum(1 for k in ALL_ON if ALL_ON[k] != c[k])
        assert diff == 1
    assert NB_SIL in nb and NB_LEN in nb and NB_LOUD in nb


def test_explores_neighbor_of_best():
    nxt = next_config([{"config": ALL_ON, "pct": 0.40}])
    assert nxt in neighbors(ALL_ON)        # best=all-on 의 미시도 이웃


def test_moves_to_better_neighbor():
    rounds = [{"config": ALL_ON, "pct": 0.40}, {"config": NB_SIL, "pct": 0.55}]
    nxt = next_config(rounds)              # best=NB_SIL → 그 이웃 탐색
    assert nxt in neighbors(NB_SIL)
    assert nxt != ALL_ON                   # 이미 시도됨


def test_converges_to_none():
    # all-on 이 best, 세 이웃 모두 시도(더 나음 없음) → 수렴
    rounds = [{"config": ALL_ON, "pct": 0.40},
              {"config": NB_SIL, "pct": 0.30},
              {"config": NB_LEN, "pct": 0.30},
              {"config": NB_LOUD, "pct": 0.35}]
    assert next_config(rounds) is None


def test_best_round_picks_max_pct():
    rounds = [{"config": ALL_ON, "pct": 0.40}, {"config": NB_SIL, "pct": 0.55},
              {"config": NB_LEN, "pct": None}]
    assert best_round(rounds)["config"] == NB_SIL
    assert best_round([]) is None
    assert best_round([{"config": ALL_ON, "pct": None}]) is None


def test_config_to_flags():
    assert config_to_flags(ALL_ON) == ["--silence-profile", "aggressive",
                                        "--length-profile", "tight", "--loudness-lufs", "-14"]


def test_partition_provenance_splits_verified_and_unknown():
    # Codex #2: 루프 코호트는 우리 생성·발행(provenance)만 — 미확인은 분리되어 차단됨
    verified, unknown = partition_provenance(["A", "B", "C"], {"A", "C"})
    assert verified == ["A", "C"]      # 순서 보존
    assert unknown == ["B"]


def test_partition_provenance_all_unknown():
    assert partition_provenance(["X", "Y"], set()) == ([], ["X", "Y"])


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
