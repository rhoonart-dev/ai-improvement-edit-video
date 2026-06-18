"""publish_youtube.build_snippet / token_env_name 순수 함수 단위테스트.
실행: python scripts/test_publish_youtube.py  또는  pytest scripts/test_publish_youtube.py
"""
from __future__ import annotations

import publish_youtube as pub


def test_snippet_newline_and_tags():
    s = pub.build_snippet("줄1\n줄2", ["#로맨스", "코미디"])
    assert s["title"] == "줄1 줄2"
    assert s["tags"] == ["로맨스", "코미디"]
    assert s["description"] == "#로맨스 #코미디"
    assert s["categoryId"] == pub.CATEGORY_ENTERTAINMENT


def test_snippet_title_max_100():
    assert len(pub.build_snippet("가" * 150)["title"]) == 100


def test_snippet_empty():
    s = pub.build_snippet("", None)
    assert s["title"] == "shorts" and s["tags"] == [] and s["description"] == ""


def test_token_env_name():
    assert pub.token_env_name("스토리순삭") == "YT_REFRESH_TOKEN_STORYSUNSAK"
    assert pub.token_env_name("재미쇼츠") == "YT_REFRESH_TOKEN_JAEMISHOTS"
    assert pub.token_env_name("미등록채널") == "YT_REFRESH_TOKEN"


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"{len(fns)} passed")


if __name__ == "__main__":
    _run()
