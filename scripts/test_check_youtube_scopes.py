"""check_youtube_scopes 순수 함수 단위테스트 + scope 선언 회귀 방지.
실행: python scripts/test_check_youtube_scopes.py  또는  pytest scripts/test_check_youtube_scopes.py
"""
from __future__ import annotations

import check_youtube_scopes as c

UPLOAD = "https://www.googleapis.com/auth/youtube.upload"
READONLY = "https://www.googleapis.com/auth/youtube.readonly"
FULL = "https://www.googleapis.com/auth/youtube"
FORCE_SSL = "https://www.googleapis.com/auth/youtube.force-ssl"


def test_upload_plus_readonly_cannot_transition():
    """2026-07-27 사고의 실제 조합 — 업로드는 되지만 공개 전환은 불가."""
    assert c.can_transition([UPLOAD, READONLY]) is False


def test_full_scope_can_transition():
    assert c.can_transition([UPLOAD, FULL]) is True


def test_force_ssl_can_transition():
    assert c.can_transition([FORCE_SSL]) is True


def test_empty_and_none_cannot_transition():
    assert c.can_transition([]) is False
    assert c.can_transition(None) is False


def test_missing_env_keys_reports_all_three(monkeypatch=None):
    """env 가 비면 클라이언트 2개 + 토큰 1개 이름이 나온다(어느 키인지 사람이 알 수 있게)."""
    missing = c.missing_env_keys("재미쇼츠", env={})
    assert len(missing) == 3
    assert any(k.startswith("YT_REFRESH_TOKEN_") for k in missing)
    assert any(k.startswith("YT_CLIENT_ID") for k in missing)
    assert any(k.startswith("YT_CLIENT_SECRET") for k in missing)


def test_missing_env_keys_empty_when_all_present():
    keys = c.missing_env_keys("재미쇼츠", env={})
    env = {k: "x" for k in keys}
    assert c.missing_env_keys("재미쇼츠", env=env) == []


def test_verdict_line_marks_bad_channel_and_error():
    bad = c.verdict_line("재미쇼츠", [UPLOAD, READONLY])
    assert "⛔" in bad and "재발급" in bad
    ok = c.verdict_line("재미쇼츠", [FULL])
    assert "✓" in ok
    err = c.verdict_line("재미쇼츠", [], error="unauthorized_client")
    assert "✗" in err and "unauthorized_client" in err


def test_issuer_requests_transition_scope():
    """발급 스크립트가 공개 전환 가능한 scope 를 요청하는지 — 빠지면 사고가 재발한다."""
    import get_youtube_token as g
    assert c.can_transition(g.SCOPES), f"발급 SCOPES 에 videos.update 권한이 없다: {g.SCOPES}"
    assert UPLOAD in g.SCOPES


def test_publish_credentials_do_not_narrow_scope():
    """Credentials(scopes=…) 로 access token 을 좁히면 조회·전환이 403 이 된다 — 선언 금지."""
    import inspect

    import publish_youtube as p
    src = inspect.getsource(p._credentials)
    assert "scopes=" not in src, "publish 자격증명이 scope 를 선언하면 access token 이 좁혀진다"


if __name__ == "__main__":
    import sys
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ✓ {name}")
            except AssertionError as e:
                fails += 1
                print(f"  ✗ {name}: {e}")
    print(f"\n{'실패 ' + str(fails) + '건' if fails else '전부 통과'}")
    sys.exit(1 if fails else 0)
