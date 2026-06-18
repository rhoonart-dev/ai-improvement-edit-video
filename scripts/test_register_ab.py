#!/usr/bin/env python3
"""register_ab_experiment.build_rows 테스트 — 쌍 → treatment/control 2행, params·pair_id 정확.
실행: /Users/gimsewon/rhoonart/ai-video/.venv/bin/python -m pytest scripts/test_register_ab.py -q"""
from register_ab_experiment import build_rows


def test_two_rows_per_pair_with_arms():
    pairs = [{"source_work": "로맨스의 절댓값", "treatment_vid": "AAA", "control_vid": "BBB"}]
    rows = build_rows("loudness_v1", pairs)
    assert len(rows) == 2
    arms = {r["arm"]: r for r in rows}
    assert arms["treatment"]["video_external_id"] == "AAA"
    assert arms["control"]["video_external_id"] == "BBB"
    # 같은 쌍은 같은 pair_id
    assert arms["treatment"]["pair_id"] == arms["control"]["pair_id"]


def test_loudness_params_per_arm():
    rows = build_rows("loudness_v1", [{"source_work": "W", "treatment_vid": "T", "control_vid": "C"}])
    arms = {r["arm"]: r["treatment_params"] for r in rows}
    assert arms["treatment"] == {"loudness_target_lufs": -14}
    assert arms["control"] == {"loudness_target_lufs": None}


def test_storyline_key_used_as_pair_id():
    rows = build_rows("loudness_v1", [{"source_work": "W", "treatment_vid": "T",
                                       "control_vid": "C", "storyline_key": "ep1_clip2"}])
    assert all(r["pair_id"] == "ep1_clip2" for r in rows)


def test_distinct_pair_ids_across_pairs():
    pairs = [{"source_work": "W", "treatment_vid": "T1", "control_vid": "C1"},
             {"source_work": "W", "treatment_vid": "T2", "control_vid": "C2"}]
    rows = build_rows("loudness_v1", pairs)
    pids = {r["pair_id"] for r in rows}
    assert len(pids) == 2          # 같은 작품이라도 쌍마다 다른 pair_id (index)
    assert len(rows) == 4


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
