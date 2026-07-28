"""channel_registry 단위테스트 — env 키 규약 · resolve 우선순위 · targets/names · 백필 ·
작품명 정규화(시즌 보존) · 머신 식별 · 작품 카드 · 유효 설정 조립.
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
    # 실제 config/channels.json: token_slug 유일, 기존 채널 슬러그 보존
    # (채널 수는 단언하지 않는다 — 수는 파일이 정본이고 자주 늘어난다)
    recs = reg.load_channels()
    slugs = [r["token_slug"] for r in recs]
    assert len(slugs) == len(set(slugs)), "token_slug 중복"
    assert "CINEMAINBED" in slugs and "JAEMISHOTS" in slugs
    assert reg.resolve("이불 속 극장", records=recs)["token_slug"] == "CINEMAINBED"
    assert reg.resolve("재미쇼츠", records=recs)["token_slug"] == "JAEMISHOTS"


# ── 작품명 정규화 (권리 경로용) ──

def test_norm_work_title_keeps_season_marker():
    # factory/cluster._norm_title 은 시즌 표기를 지운다 → 어느 라이선스 행을 쓸지 가르는 구분이
    # 사라진다. 권리 경로용 정규화는 반드시 시즌을 보존해야 한다.
    a, b = "SNL 코리아 리부트 시즌7", "SNL 코리아 리부트 시즌8"
    assert reg.norm_work_title(a) != reg.norm_work_title(b)
    assert not reg.same_work_title(a, b)


def test_norm_work_title_folds_whitespace_and_nfd():
    assert reg.norm_work_title("언더커버 셰프") == reg.norm_work_title("언더커버셰프")
    # macOS 는 한글을 NFD 로 주는 경우가 있다 — 눈으로 같아도 바이트가 다르면 dict/SQL 조회가 실패
    import unicodedata
    nfd = unicodedata.normalize("NFD", "도깨비 10주년 여행")
    assert nfd != "도깨비 10주년 여행"
    assert reg.same_work_title(nfd, "도깨비 10주년 여행")


# ── 머신 식별 ──

ASSIGN = {"machines": {
    "mac-a": {"aliases": {"hostname": ["aaa-macmini"], "user": ["usera"]},
              "channels": ["재미쇼츠"]},
    "mac-b": {"aliases": {"hostname": ["bbb-macmini"], "user": ["userb"]},
              "channels": ["다람쥐 숏토리"]},
}}


def test_detect_machine_id_priority_and_autodetect():
    assert reg.detect_machine_id(ASSIGN, explicit="mac-b", env="mac-a") == "mac-b"
    assert reg.detect_machine_id(ASSIGN, env="mac-a", local="mac-b") == "mac-a"
    assert reg.detect_machine_id(ASSIGN, hostname="AAA-MacMini.local", user="") == "mac-a"
    assert reg.detect_machine_id(ASSIGN, hostname="nope", user="userb") == "mac-b"


def test_detect_machine_id_never_guesses():
    # 🛑 미해결·다중매칭에서 '전 채널' 로 폴백하면 남의 채널까지 생성한다 → 반드시 예외
    for kw in ({"explicit": "no-such"}, {"hostname": "unknown-host", "user": "nobody"}):
        try:
            reg.detect_machine_id(ASSIGN, **kw)
            raise AssertionError(f"예외가 나야 한다: {kw}")
        except LookupError:
            pass


def test_detect_machine_id_rejects_multi_match():
    amb = {"machines": {
        "m1": {"aliases": {"hostname": ["shared"]}, "channels": []},
        "m2": {"aliases": {"hostname": ["shared"]}, "channels": []},
    }}
    try:
        reg.detect_machine_id(amb, hostname="shared-box", user="")
        raise AssertionError("다중매칭은 예외여야 한다")
    except LookupError:
        pass


def test_machine_channels():
    assert reg.machine_channels("mac-a", ASSIGN) == ["재미쇼츠"]


# ── 채널 ↔ 작품 ──

def test_works_of_and_channels_of_work():
    assert reg.works_of("재미쇼츠", RECS) == ["유미의 세포들 시즌3"]
    two = [{"name": "킥킥극장", "works": ["SNL 시즌8"]}, {"name": "몰입도둑", "works": ["SNL 시즌8"]}]
    assert reg.channels_of_work("SNL 시즌8", two) == ["킥킥극장", "몰입도둑"]


WORKS = {
    "도깨비 10주년 여행": {
        "source": {"type": "youtube_playlist", "url": "https://www.youtube.com/playlist?list=PLx",
                   "episode_regex": r"\bEP[.\s]?(\d{1,3})\b", "start_episode": 1,
                   "min_source_duration_sec": 500},
        "constraints": {"geoblock_required": False, "subtitles": "none"}},
    "로맨스의 절댓값": {
        "source": {"type": "local", "dir_slug": "romance", "file_glob": "EP*.mp4",
                   "episode_regex": r"EP(\d+)", "start_episode": 1},
        "constraints": {"geoblock_required": False, "subtitles": "provided"}},
}
POLICY = {"gen_flags_base": ["--length-profile", "tight"]}


def test_work_card_exact_only_with_candidates():
    assert reg.work_card("도깨비 10주년 여행", WORKS) is not None
    assert reg.work_card("도깨비10주년여행", WORKS) is None       # 완전일치만
    assert reg.work_card_candidates("도깨비10주년여행", WORKS) == ["도깨비 10주년 여행"]


# ── 유효 설정 ──

def test_effective_config_youtube_uses_legacy_keys():
    recs = [{"name": "숏테토칩", "works": ["도깨비 10주년 여행"]}]
    asg = {"machines": {"m": {"channels": ["숏테토칩"]}}}
    got = reg.effective_channel_configs("m", records=recs, works=WORKS, assignments=asg,
                                        policy=POLICY, sources_root="/tmp/src", machine_local={})
    c = got[0]
    assert c["source_type"] == "youtube"                      # scene_loop 가 아는 값
    assert c["source_url"].endswith("list=PLx")
    assert c["title_episode_regex"] and c["min_source_duration_sec"] == 500
    assert c["gen_flags"] == ["--length-profile", "tight", "--no-subtitles"]
    assert c["_source_kind"] == "youtube_playlist"            # 권리 범위 assert 용


def test_effective_config_local_composes_path_and_keeps_subtitles():
    recs = [{"name": "이불 속 극장", "works": ["로맨스의 절댓값"]}]
    asg = {"machines": {"m": {"channels": ["이불 속 극장"]}}}
    c = reg.effective_channel_configs("m", records=recs, works=WORKS, assignments=asg,
                                      policy=POLICY, sources_root="/tmp/src", machine_local={})[0]
    assert c["source_type"] == "local"
    assert c["source_dir"] == "/tmp/src/romance"              # 경로만 머신별로 합성
    assert c["video_glob"] == "EP*.mp4" and c["episode_regex"] == r"EP(\d+)"
    assert "--no-subtitles" not in c["gen_flags"]             # subtitles=provided


def test_effective_config_missing_card_raises_with_candidate():
    recs = [{"name": "숏테토칩", "works": ["도깨비10주년여행"]}]   # 공백 없는 오기
    asg = {"machines": {"m": {"channels": ["숏테토칩"]}}}
    try:
        reg.effective_channel_configs("m", records=recs, works=WORKS, assignments=asg,
                                      policy=POLICY, sources_root="/tmp", machine_local={})
        raise AssertionError("카드 없으면 예외여야 한다")
    except ValueError as e:
        assert "도깨비 10주년 여행" in str(e)                  # 후보를 알려준다


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"{len(fns)} passed")


if __name__ == "__main__":
    _run()
