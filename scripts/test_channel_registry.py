"""channel_registry 단위테스트 — env 키 규약 · resolve 우선순위 · targets/names · 백필.
실행: python scripts/test_channel_registry.py  또는  pytest scripts/test_channel_registry.py
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import channel_registry as reg

# 픽스처 레코드(실제 config에 의존하지 않음)
RECS = [
    {"token_slug": "STORYSUNSAK", "name": "스토리순삭", "handle": "@스토리순삭",
     "channel_id": None, "gcp_project": "DEFAULT", "works": ["로맨스의 절댓값"]},
    {"token_slug": "JAEMISHOTS", "name": "재미쇼츠", "handle": None,
     "channel_id": "UC7eXwtR1TyUVe2ts6BUjXGA", "gcp_project": "DEFAULT", "works": ["유미의 세포들 시즌3"]},
    {"token_slug": "DARAMJI", "name": "다람쥐 숏토리", "handle": "@다람쥐숏토리",
     "channel_id": None, "gcp_project": "P2", "works": ["아파트", "신입사원 강회장"]},
]


# ── env 키 규약 ──

def test_token_env_name():
    assert reg.token_env_name("STORYSUNSAK") == "YT_REFRESH_TOKEN_STORYSUNSAK"
    assert reg.token_env_name(None) == "YT_REFRESH_TOKEN"
    assert reg.token_env_name("") == "YT_REFRESH_TOKEN"


def test_client_env_names_default_is_global():
    # DEFAULT/빈값 → 전역 키(기존 2채널 무중단)
    assert reg.client_env_names("DEFAULT") == ("YT_CLIENT_ID", "YT_CLIENT_SECRET")
    assert reg.client_env_names(None) == ("YT_CLIENT_ID", "YT_CLIENT_SECRET")
    assert reg.client_env_names("") == ("YT_CLIENT_ID", "YT_CLIENT_SECRET")


def test_client_env_names_project_suffix():
    assert reg.client_env_names("P2") == ("YT_CLIENT_ID_P2", "YT_CLIENT_SECRET_P2")


# ── resolve 우선순위 ──

def test_resolve_by_channel_id_wins():
    r = reg.resolve(records=RECS, channel_id="UC7eXwtR1TyUVe2ts6BUjXGA", name="엉뚱한이름")
    assert r["token_slug"] == "JAEMISHOTS"


def test_resolve_by_handle():
    assert reg.resolve(records=RECS, handle="@스토리순삭")["token_slug"] == "STORYSUNSAK"
    assert reg.resolve(records=RECS, handle="다람쥐숏토리")["token_slug"] == "DARAMJI"  # @ 없이도


def test_resolve_by_name_whitespace_insensitive():
    assert reg.resolve("스토리 순삭", records=RECS)["token_slug"] == "STORYSUNSAK"
    assert reg.resolve("다람쥐숏토리", records=RECS)["token_slug"] == "DARAMJI"


def test_resolve_unknown_is_none():
    assert reg.resolve("전혀없는채널", records=RECS) is None
    assert reg.resolve(records=RECS, channel_id="UCnope") is None
    assert reg.resolve(records=RECS) is None


def test_resolve_priority_channel_id_over_name_conflict():
    # channel_id는 재미쇼츠인데 name은 스토리순삭 → channel_id 우선
    r = reg.resolve(records=RECS, channel_id="UC7eXwtR1TyUVe2ts6BUjXGA", handle="@스토리순삭")
    assert r["token_slug"] == "JAEMISHOTS"


# ── targets / channel_names ──

def test_targets_flattens_works():
    t = reg.targets(RECS)
    assert ("로맨스의 절댓값", "스토리순삭") in t
    assert ("아파트", "다람쥐 숏토리") in t
    assert ("신입사원 강회장", "다람쥐 숏토리") in t
    assert len(t) == 4  # 1 + 1 + 2


def test_channel_names():
    assert reg.channel_names(RECS) == ("스토리순삭", "재미쇼츠", "다람쥐 숏토리")


# ── load / backfill (파일 I/O) ──

def test_load_channels_missing_file_returns_empty():
    assert reg.load_channels("/nonexistent/path/channels.json") == []


def test_backfill_channel_id_sets_when_empty():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "channels.json"
        p.write_text(json.dumps([{"token_slug": "STORYSUNSAK", "name": "스토리순삭", "channel_id": None}],
                                ensure_ascii=False), encoding="utf-8")
        assert reg.backfill_channel_id("STORYSUNSAK", "UCabc123", path=p) is True
        recs = json.loads(p.read_text(encoding="utf-8"))
        assert recs[0]["channel_id"] == "UCabc123"


def test_backfill_channel_id_noop_when_already_set():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "channels.json"
        p.write_text(json.dumps([{"token_slug": "JAEMISHOTS", "channel_id": "UCorig"}],
                                ensure_ascii=False), encoding="utf-8")
        assert reg.backfill_channel_id("JAEMISHOTS", "UCnew", path=p) is False
        recs = json.loads(p.read_text(encoding="utf-8"))
        assert recs[0]["channel_id"] == "UCorig"  # 덮어쓰지 않음


def test_real_config_loads_and_is_consistent():
    # 실제 config/channels.json: token_slug 유일, 10채널, 기존 2채널 슬러그 보존
    recs = reg.load_channels()
    slugs = [r["token_slug"] for r in recs]
    assert len(slugs) == len(set(slugs)), "token_slug 중복"
    assert "STORYSUNSAK" in slugs and "JAEMISHOTS" in slugs
    assert reg.resolve("스토리순삭", records=recs)["token_slug"] == "STORYSUNSAK"
    assert reg.resolve("재미쇼츠", records=recs)["token_slug"] == "JAEMISHOTS"


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"{len(fns)} passed")


if __name__ == "__main__":
    _run()
