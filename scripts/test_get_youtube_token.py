"""get_youtube_token 순수 함수 단위테스트(upsert_env_text, resolve_record).
실행: python scripts/test_get_youtube_token.py  또는  pytest scripts/test_get_youtube_token.py
"""
from __future__ import annotations

import get_youtube_token as g

# 픽스처 레코드(실제 config에 의존하지 않음)
RECS = [
    {"token_slug": "STORYSUNSAK", "name": "스토리순삭", "handle": "@스토리순삭",
     "channel_id": None, "gcp_project": "DEFAULT"},
    {"token_slug": "JAEMISHOTS", "name": "재미쇼츠", "handle": None,
     "channel_id": "UC7eXwtR1TyUVe2ts6BUjXGA", "gcp_project": "DEFAULT"},
]


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


def test_resolve_record_auto_detect_by_channel_id():
    # channel_id 정확매칭 → 지정 없이도 확정
    rec, ok, msg = g.resolve_record("재미쇼츠", "UC7eXwtR1TyUVe2ts6BUjXGA", None, None, RECS)
    assert rec["token_slug"] == "JAEMISHOTS" and ok and not msg


def test_resolve_record_auto_detect_by_handle():
    rec, ok, _ = g.resolve_record("스토리 순삭", None, "@스토리순삭", None, RECS)
    assert rec["token_slug"] == "STORYSUNSAK" and ok


def test_resolve_record_unregistered_no_intended_is_held():
    # 미등록 채널(개인 등) + --channel 미지정 → 저장 보류
    rec, ok, msg = g.resolve_record("Laeebly", "UCpersonal", None, None, RECS)
    assert rec is None and ok is False and msg


def test_resolve_record_mismatch_refused_then_forced():
    # 지정은 스토리순삭인데 실제 잡힌 channel_id는 재미쇼츠 → 거부, force면 통과
    rec, ok, msg = g.resolve_record("재미쇼츠", "UC7eXwtR1TyUVe2ts6BUjXGA", None, "스토리순삭", RECS)
    assert rec["token_slug"] == "STORYSUNSAK" and ok is False and msg
    rec2, ok2, _ = g.resolve_record("재미쇼츠", "UC7eXwtR1TyUVe2ts6BUjXGA", None, "스토리순삭", RECS, force=True)
    assert rec2["token_slug"] == "STORYSUNSAK" and ok2 is True


def test_resolve_record_intended_not_in_config_refused():
    rec, ok, msg = g.resolve_record("아무개", None, None, "없는채널", RECS)
    assert rec is None and ok is False and "config" in msg


def test_resolve_record_detect_failed_trusts_intended():
    # 자동확인 실패(신호 전무) + --channel 지정 → 지정값 신뢰(경고 동반)
    rec, ok, msg = g.resolve_record(None, None, None, "재미쇼츠", RECS)
    assert rec["token_slug"] == "JAEMISHOTS" and ok is True and msg


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"{len(fns)} passed")


if __name__ == "__main__":
    _run()
