"""evaluate_run.gate 순수 함수 단위테스트 — judge=안전 게이트(성과 아님) 의미.
실행: python scripts/test_evaluate_run.py  또는  pytest scripts/test_evaluate_run.py
"""
from __future__ import annotations

import evaluate_run as er


def test_gate_pass_when_safe():
    # 안전(환각無)하면 floor 없을 때 PASS — judge quality 는 성과 게이트가 아님
    assert er.gate(0.8, False)[0] == "PASS"


def test_gate_low_quality_still_pass_without_floor():
    # 핵심: 낮은 quality 도 floor 미지정이면 PASS (승격은 발행 후 벤치마크/+14일이 판정)
    assert er.gate(0.4, False)[0] == "PASS"


def test_gate_discard_on_hallucination():
    # 환각은 quality 무관 DISCARD (안전)
    assert er.gate(0.95, True)[0] == "DISCARD"


def test_gate_review_when_quality_none():
    assert er.gate(None, False)[0] == "REVIEW"


def test_gate_regenerate_below_safety_floor():
    # opt-in safety_floor 미만 = 명백히 깨짐 → REGENERATE
    assert er.gate(0.1, False, 0.2)[0] == "REGENERATE"


def test_gate_pass_above_safety_floor():
    assert er.gate(0.5, False, 0.2)[0] == "PASS"


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"{len(fns)} passed")


if __name__ == "__main__":
    _run()
