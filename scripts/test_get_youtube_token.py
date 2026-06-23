"""get_youtube_token 순수 함수 단위테스트(upsert_env_text, match_channel).
실행: python scripts/test_get_youtube_token.py  또는  pytest scripts/test_get_youtube_token.py
"""
from __future__ import annotations

import get_youtube_token as g


def test_upsert_replaces_existing():
    out = g.upsert_env_text("A=1\nYT_CLIENT_ID=old\nB=2\n", {"YT_CLIENT_ID": "new"})
    assert "YT_CLIENT_ID=new" in out
    assert "YT_CLIENT_ID=old" not in out
    assert "A=1" in out and "B=2" in out


def test_upsert_appends_missing():
    out = g.upsert_env_text("A=1\n", {"YT_REFRESH_TOKEN_STORYSUNSAK": "tok"})
    assert "A=1" in out
    assert "YT_REFRESH_TOKEN_STORYSUNSAK=tok" in out


def test_upsert_dedups_duplicate_keys():
    out = g.upsert_env_text("K=1\nK=2\n", {"K": "3"})
    assert out.count("K=") == 1
    assert "K=3" in out


def test_upsert_empty_file():
    out = g.upsert_env_text("", {"YT_CLIENT_ID": "x"})
    assert out.strip() == "YT_CLIENT_ID=x"


def test_upsert_trailing_newline():
    assert g.upsert_env_text("A=1\n", {"B": "2"}).endswith("\n")


def test_upsert_preserves_comments_and_blanks():
    out = g.upsert_env_text("# c\n\nA=1\n", {"B": "2"})
    assert "# c" in out and "A=1" in out and "B=2" in out


def test_match_channel_exact_and_substring():
    assert g.match_channel("스토리순삭") == "스토리순삭"
    assert g.match_channel("재미쇼츠 공식채널") == "재미쇼츠"  # 표시명이 더 길어도 부분일치
    assert g.match_channel("스토리 순삭") == "스토리순삭"  # 공백 무시
    assert g.match_channel("전혀다른채널") is None
    assert g.match_channel("Laeebly") is None  # 개인 채널 → 미매칭
    assert g.match_channel("") is None
    assert g.match_channel(None) is None


def test_resolve_key_auto_detect_match():
    key, ok, msg = g.resolve_key("스토리순삭", None)
    assert key == "YT_REFRESH_TOKEN_STORYSUNSAK" and ok and not msg


def test_resolve_key_personal_channel_no_intended_is_held():
    # Laeebly(개인) + --channel 미지정 → 저장 보류
    key, ok, msg = g.resolve_key("Laeebly", None)
    assert key == "YT_REFRESH_TOKEN" and ok is False and msg


def test_resolve_key_mismatch_refused_then_forced():
    # 지정은 스토리순삭인데 실제 잡힌 건 재미쇼츠 → 거부, force면 통과
    key, ok, msg = g.resolve_key("재미쇼츠 공식", "스토리순삭")
    assert key == "YT_REFRESH_TOKEN_STORYSUNSAK" and ok is False and msg
    key2, ok2, _ = g.resolve_key("재미쇼츠 공식", "스토리순삭", force=True)
    assert key2 == "YT_REFRESH_TOKEN_STORYSUNSAK" and ok2 is True


def test_resolve_key_personal_with_intended_refused():
    # Laeebly 잡혔는데 스토리순삭 지정 → 확신 불가로 거부(브랜드 채널 재선택 유도)
    key, ok, msg = g.resolve_key("Laeebly", "스토리순삭")
    assert key == "YT_REFRESH_TOKEN_STORYSUNSAK" and ok is False and msg


def test_resolve_key_detect_failed_trusts_intended():
    # 자동확인 실패(title=None) + --channel 지정 → 지정값 신뢰(경고 동반)
    key, ok, msg = g.resolve_key(None, "재미쇼츠")
    assert key == "YT_REFRESH_TOKEN_JAEMISHOTS" and ok is True and msg


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"{len(fns)} passed")


if __name__ == "__main__":
    _run()
