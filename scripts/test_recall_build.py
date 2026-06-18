"""recall_build.topic_recall 순수 메트릭 단위테스트.
실행: python scripts/test_recall_build.py  또는  pytest scripts/test_recall_build.py
"""
from __future__ import annotations

import recall_build as rb

HUMANS = [
    ("수학 4점 맞은 학생의 기상천외한 반성문 #로맨스의절대값", 1_000_000),
    ("쿨한 척하는 추성훈이 딸의 남자친구 #snl", 5_000),
    ("우주 탐험 다큐멘터리 풀버전", 2_000),
]


def test_recall_high_when_topic_matches_hit():
    m = rb.topic_recall("수학 4점 맞은 학생의 기상천외한 반성문", HUMANS, k=3)
    assert m["recall_at_k"] > 0.6
    assert "수학 4점" in (m["nearest"] or "")
    assert m["k"] == 3


def test_recall_low_when_unrelated():
    m = rb.topic_recall("완전 무관한 제목 김치찌개 끓이는 법", HUMANS, k=3)
    assert m["recall_at_k"] < 0.4


def test_weighted_recall_dominated_by_high_views():
    m = rb.topic_recall("수학 4점 맞은 학생의 기상천외한 반성문", HUMANS, k=3)
    assert m["weighted_recall"] > 0.5


def test_empty_humans():
    m = rb.topic_recall("x", [], k=3)
    assert m["recall_at_k"] is None and m["k"] == 0


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"{len(fns)} passed")


if __name__ == "__main__":
    _run()
