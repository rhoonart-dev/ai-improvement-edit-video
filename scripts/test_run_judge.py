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


def test_sensitive_fields_parsed_and_sanitized():
    """민감 소재 감지(v2) — 표시 전용 필드. 종류는 정본 목록 밖 값을 버리고,
    kinds 가 있으면 flag 를 켠다(모델이 flag 를 빼먹어도 종류가 증거)."""
    j = rj.parse_judge_json(
        '{"hook_3s":0.5,"visual_hook":0.5,"pacing":0.5,"completion_pull":0.5,'
        '"sensitive_flag":true,"sensitive_kinds":["정치","엉뚱한값","성적표현"],'
        '"sensitive_note":"대통령 패러디 콩트"}')
    rs = j["rubric_scores"]
    assert rs["sensitive_flag"] is True
    assert rs["sensitive_kinds"] == ["정치", "성적표현"]      # 목록 밖 값 제거
    assert rs["sensitive_note"] == "대통령 패러디 콩트"
    # 점수는 민감 소재와 무관해야 한다 — 표시 전용
    assert j["quality_score"] == 0.5


def test_sensitive_defaults_when_absent():
    """구버전 응답(민감 필드 없음)도 깨지지 않는다 — flag False·kinds []·note ''."""
    j = rj.parse_judge_json('{"hook_3s":1,"visual_hook":1,"pacing":1,"completion_pull":1}')
    rs = j["rubric_scores"]
    assert rs["sensitive_flag"] is False and rs["sensitive_kinds"] == [] and rs["sensitive_note"] == ""


def test_sensitive_kinds_without_flag_still_flags():
    j = rj.parse_judge_json(
        '{"hook_3s":1,"visual_hook":1,"pacing":1,"completion_pull":1,"sensitive_kinds":["정치"]}')
    assert j["rubric_scores"]["sensitive_flag"] is True


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
