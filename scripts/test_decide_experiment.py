"""decide_experiment.pick_winner 순수 함수 단위테스트.
실행: python scripts/test_decide_experiment.py  또는  pytest scripts/test_decide_experiment.py
"""
from __future__ import annotations

import decide_experiment as de


def test_clear_winner():
    w, _ = de.pick_winner({"a": 0.85, "b": 0.75}, 0.03)
    assert w == "a"


def test_tie_within_margin():
    w, _ = de.pick_winner({"a": 0.80, "b": 0.79}, 0.03)
    assert w is None


def test_missing_score():
    w, _ = de.pick_winner({"a": 0.8, "b": None}, 0.03)
    assert w is None


def test_challenger_wins():
    w, _ = de.pick_winner({"champion": 0.70, "challenger": 0.86}, 0.03)
    assert w == "challenger"


def test_scorers_routing():
    # 승격 신호 라우팅: benchmark(증거일관 기본) · judge(빠른 프록시)
    assert de.SCORERS["benchmark"] is de._benchmark_score
    assert de.SCORERS["judge"] is de._latest_judge


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"{len(fns)} passed")


if __name__ == "__main__":
    _run()
