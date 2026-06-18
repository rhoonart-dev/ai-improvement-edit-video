"""run_judge.parse_judge_json 순수 로직 단위테스트.
실행: python scripts/test_run_judge.py  또는  pytest scripts/test_run_judge.py
"""
from __future__ import annotations

import run_judge as rj


def test_parse_clean():
    j = rj.parse_judge_json(
        '{"hook_3s":0.8,"visual_hook":0.6,"pacing":0.7,"completion_pull":0.5,'
        '"hashtags_ok":true,"hallucination_flag":false,"confidence":0.9,"rationale":"ok"}')
    assert j["quality_score"] == round((0.8 + 0.6 + 0.7 + 0.5) / 4, 4)
    assert j["rubric_scores"]["hashtags_ok"] is True
    assert j["rubric_scores"]["hallucination_flag"] is False
    assert j["confidence"] == 0.9


def test_parse_codefence_and_surrounding_text():
    txt = '결과입니다:\n```json\n{"hook_3s":1,"visual_hook":1,"pacing":1,"completion_pull":1}\n```\n끝'
    j = rj.parse_judge_json(txt)
    assert j["quality_score"] == 1.0


def test_clamp_out_of_range():
    j = rj.parse_judge_json('{"hook_3s":1.5,"visual_hook":-0.2,"pacing":0.5,"completion_pull":0.5}')
    assert j["quality_score"] == 0.5          # (1.0+0.0+0.5+0.5)/4


def test_missing_dim_is_none():
    j = rj.parse_judge_json('{"hook_3s":0.8,"pacing":0.4}')
    assert j["quality_score"] == round((0.8 + 0.4) / 2, 4)
    assert j["rubric_scores"]["visual_hook"] is None


def test_no_json_raises():
    raised = False
    try:
        rj.parse_judge_json("no json here")
    except ValueError:
        raised = True
    assert raised


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"{len(fns)} passed")


if __name__ == "__main__":
    _run()
