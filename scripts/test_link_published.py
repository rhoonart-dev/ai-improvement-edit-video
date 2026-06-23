"""link_published 순수 로직 단위테스트 (DB 무관).
실행: python scripts/test_link_published.py   또는   pytest scripts/test_link_published.py
"""
from __future__ import annotations

from datetime import datetime

import link_published as lp


def test_parse_published_at_none_and_empty():
    assert lp.parse_published_at(None) is None
    assert lp.parse_published_at("") is None


def test_parse_published_at_iso():
    dt = lp.parse_published_at("2026-06-16T09:00:00+09:00")
    assert isinstance(dt, datetime)
    assert (dt.year, dt.month, dt.day) == (2026, 6, 16)
    assert dt.utcoffset() is not None          # 타임존 보존


def test_parse_published_at_invalid_raises():
    raised = False
    try:
        lp.parse_published_at("not-a-date")
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
