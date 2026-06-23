"""add_golden_label.agreement 순수 함수 단위테스트.
실행: python scripts/test_add_golden_label.py  또는  pytest scripts/test_add_golden_label.py
"""
from __future__ import annotations

import add_golden_label as ag


def test_agreement_mae():
    r = ag.agreement([(0.8, 0.9), (0.6, 0.5)])
    assert r["n"] == 2 and r["mae"] == 0.1


def test_agreement_skips_none():
    r = ag.agreement([(0.8, None), (0.6, 0.6)])
    assert r["n"] == 1 and r["mae"] == 0.0


def test_agreement_empty():
    assert ag.agreement([]) is None


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"{len(fns)} passed")


if __name__ == "__main__":
    _run()
