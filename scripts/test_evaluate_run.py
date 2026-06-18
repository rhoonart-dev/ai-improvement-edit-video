"""evaluate_run.gate 순수 함수 단위테스트.
실행: python scripts/test_evaluate_run.py  또는  pytest scripts/test_evaluate_run.py
"""
from __future__ import annotations

import evaluate_run as er


def test_gate_pass():
    assert er.gate(0.8, False, 0.6)[0] == "PASS"


def test_gate_boundary_is_pass():
    assert er.gate(0.6, False, 0.6)[0] == "PASS"


def test_gate_regenerate_when_low():
    assert er.gate(0.4, False, 0.6)[0] == "REGENERATE"


def test_gate_discard_on_hallucination():
    assert er.gate(0.95, True, 0.6)[0] == "DISCARD"


def test_gate_review_when_quality_none():
    assert er.gate(None, False, 0.6)[0] == "REVIEW"


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"{len(fns)} passed")


if __name__ == "__main__":
    _run()
