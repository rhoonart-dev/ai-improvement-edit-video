"""scene_loop 순수 로직 단위테스트 — 회차 발견(로컬/유튜브)·소스 선택·명령 조립.
실행: python scripts/test_scene_loop.py  또는  pytest scripts/test_scene_loop.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import scene_loop as sl


# ── 로컬 소스: start_episode ──

def _touch_eps(d, nums):
    for n in nums:
        Path(d, f"EP{n}.mp4").touch()


def test_discover_episodes_default_starts_at_1():
    with tempfile.TemporaryDirectory() as d:
        _touch_eps(d, [1, 2, 399, 410, 425])
        assert [e for e, _ in sl.discover_episodes(d, "EP*.mp4", r"EP(\d+)")] == [1, 2, 399, 410, 425]


def test_discover_episodes_start_episode_filters():
    with tempfile.TemporaryDirectory() as d:
        _touch_eps(d, [1, 2, 399, 409, 410, 411, 425])
        got = [e for e, _ in sl.discover_episodes(d, "EP*.mp4", r"EP(\d+)", 410)]
        assert got == [410, 411, 425]


# ── 유튜브 소스 ──

def test_parse_index_lines_tab_and_literal_tab():
    real = "1068\tabc123\t제목 EP.425"
    lit = "80\\tdef456\\t[예고] 제목 EP.426"
    out = sl.parse_index_lines(real + "\n" + lit + "\n" + "쓰레기줄")
    assert out == [{"id": "abc123", "title": "제목 EP.425", "duration": 1068.0},
                   {"id": "def456", "title": "[예고] 제목 EP.426", "duration": 80.0}]


def test_parse_index_lines_handles_na_duration():
    assert sl.parse_index_lines("NA\tvid\t제목")[0]["duration"] is None


EP_RE = r"\bEP[.\s]?(\d{1,3})\b"

ENTRIES = [
    {"id": "teaser", "title": "[예고] 다음주 EP.410", "duration": 80},
    {"id": "clip", "title": "클립 EP.410", "duration": 584},
    {"id": "highlight", "title": "[#간식게임] 하이라이트 EP.410", "duration": 1166},
    {"id": "old", "title": "옛날 회차 EP.399", "duration": 1200},
    {"id": "next", "title": "다음 회차 EP.411", "duration": 900},
    {"id": "noep", "title": "회차 없는 영상", "duration": 900},
]


def test_index_episodes_respects_start_episode():
    idx = sl.index_episodes(ENTRIES, EP_RE, start_episode=410)
    assert sorted(idx) == [410, 411]          # EP.399 제외, 회차없음 제외


def test_index_episodes_min_duration_drops_teaser():
    idx = sl.index_episodes(ENTRIES, EP_RE, start_episode=410, min_duration_sec=600)
    assert [e["id"] for e in idx[410]] == ["highlight"]   # 80초 예고·584초 클립 탈락


def test_index_episodes_keeps_unknown_duration_when_no_floor():
    # 길이 미상 목록이어도 하한이 없으면 회차는 살아남아야 한다(과거 전량 탈락 버그)
    ents = [{"id": "a", "title": "제목 EP.410", "duration": None}]
    assert sorted(sl.index_episodes(ents, EP_RE, start_episode=410)) == [410]
    # 하한이 있으면 확인 불가 → 제외(예고편 위험 회피)
    assert sl.index_episodes(ents, EP_RE, start_episode=410, min_duration_sec=600) == {}


def test_pick_episode_entry_takes_longest():
    idx = sl.index_episodes(ENTRIES, EP_RE, start_episode=410)
    assert sl.pick_episode_entry(idx[410])["id"] == "highlight"
    assert sl.pick_episode_entry([]) is None


def test_channel_source_type():
    assert sl.channel_source_type({"source_dir": "/x"}) == "local"
    assert sl.channel_source_type({"source_url": "https://youtube.com/@x"}) == "youtube"
    assert sl.channel_source_type({"channel_url": "https://youtube.com/@x"}) == "youtube"  # 옛 키 호환
    assert sl.channel_source_type({"source_type": "youtube", "source_dir": "/x"}) == "youtube"


def test_source_url_accepts_channel_or_playlist():
    ch_url = "https://www.youtube.com/channel/UC.../videos"
    pl_url = "https://www.youtube.com/playlist?list=PL..."
    assert sl.source_url_of({"source_url": ch_url}) == ch_url
    assert sl.source_url_of({"source_url": pl_url}) == pl_url          # 플레이리스트 한정 작품
    assert sl.source_url_of({"channel_url": ch_url}) == ch_url         # 옛 키 폴백
    assert sl.source_url_of({}) is None


def test_discover_youtube_requires_explicit_regex():
    # 제목의 회차 표기는 작품마다 달라 기본값을 두지 않는다 — 없으면 즉시 실패해야 한다
    ch = {"work_title": "무언가", "source_url": "https://youtube.com/playlist?list=X"}
    try:
        sl.discover_episodes_youtube(None, ch, 24, lambda m: None)
        assert False, "title_episode_regex 없이 통과함"
    except ValueError as e:
        assert "title_episode_regex" in str(e)


def test_discover_youtube_requires_source_url():
    try:
        sl.discover_episodes_youtube(None, {"work_title": "무언가"}, 24, lambda m: None)
        assert False, "source_url 없이 통과함"
    except ValueError as e:
        assert "source_url" in str(e)


def test_is_url_and_source_label():
    assert sl.is_url("https://www.youtube.com/watch?v=a") and not sl.is_url("/tmp/EP1.mp4")
    assert sl.source_label("/tmp/EP1.mp4") == "EP1.mp4"
    assert sl.source_label("https://youtu.be/a") == "https://youtu.be/a"


def test_build_cmd_uses_youtube_url_for_urls():
    c = sl.build_cmd("py", "놀라운 토요일", "https://www.youtube.com/watch?v=a", "/out", [])
    assert "--youtube-url" in c and "--video" not in c
    assert c[c.index("--youtube-url") + 1] == "https://www.youtube.com/watch?v=a"


def test_build_cmd_uses_video_for_local_paths():
    c = sl.build_cmd("py", "작품", "/tmp/EP1.mp4", "/out", [])
    assert "--video" in c and "--youtube-url" not in c


def test_episode_dir_name_matches_outdir_convention():
    # outdir 생성과 회차 스캔이 같은 이름을 써야 유튜브 중복판정이 동작한다
    assert sl.episode_dir_name(5) == "ep05" and sl.episode_dir_name(410) == "ep410"


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"{len(fns)} passed")


if __name__ == "__main__":
    _run()
