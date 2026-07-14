#!/usr/bin/env python3
"""register_ab_experiment.build_rows 테스트 — 쌍 → treatment/control 2행, params·pair_id 정확.
실행: /Users/gimsewon/rhoonart/ai-video/.venv/bin/python -m pytest scripts/test_register_ab.py -q"""
from register_ab_experiment import build_rows, read_pairs


def test_read_pairs_skips_comment_and_blank_rows(tmp_path):
    """CSV 안내 주석(#)·빈 행을 스킵해야 함 — pairs 템플릿에 주석 달 수 있게(견고성)."""
    p = tmp_path / "pairs.csv"
    p.write_text(
        "source_work,treatment_video_id,control_video_id,storyline_key,channel_name\n"
        "# 안내 주석 — 이 줄은 무시돼야 함\n"
        "\n"
        "로맨스의 절댓값,AAA,BBB,k1,스토리순삭\n",
        encoding="utf-8")
    pairs = read_pairs(str(p))
    assert len(pairs) == 1
    assert pairs[0]["treatment_vid"] == "AAA" and pairs[0]["control_vid"] == "BBB"
    assert pairs[0]["storyline_key"] == "k1"


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


# ── A/B 쌍 불변식(Codex #3) — 퇴화/잘못된 쌍 등록 차단 ──
def test_rejects_identical_treatment_control():
    import pytest
    with pytest.raises(ValueError):
        build_rows("loudness_v1", [{"source_work": "W", "treatment_vid": "X", "control_vid": "X"}])


def test_rejects_missing_video_id():
    import pytest
    with pytest.raises(ValueError):
        build_rows("loudness_v1", [{"source_work": "W", "treatment_vid": "T", "control_vid": ""}])


def test_rejects_missing_source_work():
    import pytest
    with pytest.raises(ValueError):
        build_rows("loudness_v1", [{"source_work": "  ", "treatment_vid": "T", "control_vid": "C"}])


def test_valid_pair_still_builds():
    from register_ab_experiment import validate_pair
    assert validate_pair({"source_work": "W", "treatment_vid": "T", "control_vid": "C"}) == []


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
