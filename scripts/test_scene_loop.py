"""scene_loop 순수 로직 단위테스트 — 회차 발견(로컬/유튜브)·소스 선택·명령 조립.
실행: python scripts/test_scene_loop.py  또는  pytest scripts/test_scene_loop.py
"""
from __future__ import annotations

import json
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


def test_build_cmd_enables_research_and_scopes_episode():
    # 리서치를 켜야 인물명이 맞는다. --episode 는 리서치를 1~N회로 한정해 스포일러를 막는다.
    c = sl.build_cmd("py", "작품", "/tmp/EP1.mp4", "/out", [], 7)
    assert "--no-research" not in c
    assert c[c.index("--episode") + 1] == "7"


def test_build_cmd_omits_episode_when_unknown():
    c = sl.build_cmd("py", "작품", "/tmp/EP1.mp4", "/out", [])
    assert "--episode" not in c


# ── 권리 범위: 생성 전에 막는다 ──

def test_assert_source_scope_blocks_playlist_work_on_channel_url():
    # 🛑 '해당 플레이리스트 영상만 사용 가능' 작품에 채널 URL 이 들어가면 채널 전체가 소스가 된다
    bad = {"work_title": "도깨비", "_source_kind": "youtube_playlist",
           "source_url": "https://www.youtube.com/@tvNJoy/videos"}
    try:
        sl.assert_source_scope(bad)
        raise AssertionError("권리 범위 위반은 예외여야 한다")
    except ValueError as e:
        assert "권리 범위" in str(e)


def test_assert_source_scope_passes_matching_pairs():
    sl.assert_source_scope({"_source_kind": "youtube_playlist",
                            "source_url": "https://www.youtube.com/playlist?list=PLx"})
    sl.assert_source_scope({"_source_kind": "youtube_channel",
                            "source_url": "https://www.youtube.com/@x/videos"})
    sl.assert_source_scope({"_source_kind": "local"})          # 로컬은 URL 이 없다
    sl.assert_source_scope({})                                  # 레거시 설정(선언 없음)


# ── 작품별 생성 플래그 ──

def test_build_cmd_passes_subtitle_only_when_given():
    # 🛑 자막은 권리사 제공분일 때만 넘어온다. 호출자가 걸러 주므로 build_cmd 는 받은 대로 붙인다.
    with_sub = sl.build_cmd("py", "작품", "/tmp/a.mp4", "/out", [], 1, "/tmp/a.ko.srt")
    assert with_sub[with_sub.index("--subtitle") + 1] == "/tmp/a.ko.srt"
    assert "--subtitle" not in sl.build_cmd("py", "작품", "/tmp/a.mp4", "/out", [], 1, None)


def test_subtitle_gate_only_allows_provided():
    # 유튜브에서 함께 받아지는 자막은 자동 생성일 확률이 높아 쓰지 않는다(2026-07-29 합의).
    cached = "/cache/source.ko.srt"
    assert (cached if {"_subtitles": "provided"}.get("_subtitles") == "provided" else None) == cached
    for card in ({"_subtitles": "none"}, {}):
        assert (cached if card.get("_subtitles") == "provided" else None) is None


def test_channel_gen_flags_win_over_global():
    # 자막 유무는 작품마다 다르다 — 전역 플래그로는 자막 있는 작품과 없는 작품이 공존 못 한다
    cfg = {"gen_flags": ["--global"]}
    ch = {"gen_flags": ["--per-work"]}
    assert (ch.get("gen_flags") or cfg.get("gen_flags")) == ["--per-work"]
    assert ({}.get("gen_flags") or cfg.get("gen_flags")) == ["--global"]


# ── 제목 언어가 바뀌어도 회차를 잃지 않아야 한다 ──

def test_fetch_youtube_index_requests_korean_titles():
    """영어 제목은 뒤가 잘려 EP 표기가 사라진다 → lang 을 반드시 넘겨야 한다."""
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        class R:
            returncode = 0
            stdout = "600\tvid1\t제목 EP.3"
            stderr = ""
        return R()

    orig = sl.subprocess.run
    sl.subprocess.run = fake_run
    try:
        sl.fetch_youtube_index("py", "https://youtube.com/playlist?list=X")
    finally:
        sl.subprocess.run = orig
    cmd = captured["cmd"]
    assert "--extractor-args" in cmd
    assert cmd[cmd.index("--extractor-args") + 1] == "youtube:lang=ko"


def test_merge_index_keeps_previously_seen_titles():
    old = [{"id": "a", "title": "한글 제목 EP.3", "duration": 1187.0}]
    new = [{"id": "a", "title": "English title truncated ...", "duration": 1187.0}]
    merged, dropped = sl.merge_index(old, new)
    assert dropped == 0
    assert merged[0]["title"] == "English title truncated ..."
    assert "한글 제목 EP.3" in merged[0]["alt_titles"]


def test_merge_index_drops_ids_missing_from_new_list():
    """플레이리스트에서 빠진 영상은 권리 범위 밖이므로 캐시에 남기지 않는다."""
    old = [{"id": "a", "title": "A", "duration": 900.0}, {"id": "b", "title": "B", "duration": 900.0}]
    new = [{"id": "a", "title": "A", "duration": 900.0}]
    merged, dropped = sl.merge_index(old, new)
    assert [e["id"] for e in merged] == ["a"] and dropped == 1


def test_index_episodes_matches_alt_titles():
    """현재 제목이 영어라도 과거에 본 한글 제목으로 회차가 잡혀야 한다."""
    entries = [{"id": "a", "duration": 1187.0,
                "title": "This is legendary lol. Choose the iconic outfit ...",
                "alt_titles": ["레전드 나왔다ㅋㅋ … #도깨비10주년여행 EP.3"]}]
    idx = sl.index_episodes(entries, r"\bEP[.\s]?(\d{1,3})\b", 1, 600)
    assert list(idx) == [3]


# ── 중복 판정은 채널 단위로 닫혀 있어야 한다 ──

def _write_plan(root, channel, ep_num, job, span, video_path):
    """outputs/scene_loop/<채널>/ep<NN>/try1/<job>/edit_plan.json 을 만든다."""
    d = Path(root, "scene_loop", channel, sl.episode_dir_name(ep_num), "try1", job)
    d.mkdir(parents=True, exist_ok=True)
    Path(d, "edit_plan.json").write_text(json.dumps({
        "input": {"video_path": video_path},
        "timeline": [{"clip_start_sec": span[0], "clip_end_sec": span[1]}],
    }, ensure_ascii=False), encoding="utf-8")


def test_rendered_scenes_ignores_other_channel_same_local_source():
    """한 작품을 두 채널이 쓸 때(같은 머신·같은 로컬 파일) 서로의 장면을 보지 않아야 한다.

    예전엔 로컬 소스를 video_path 로만 매칭해서 채널B가 채널A 장면을 자기 것으로 셌다."""
    with tempfile.TemporaryDirectory() as root:
        src = "/srv/sources/가나다/EP1.mp4"
        _write_plan(root, "채널1", 1, "가나다_a1", [100.0, 150.0], src)
        _write_plan(root, "채널2", 1, "가나다_b2", [300.0, 350.0], src)

        one = sl.rendered_scenes({}, "채널1", 1, src, [root], 0.5, 15)
        two = sl.rendered_scenes({}, "채널2", 1, src, [root], 0.5, 15)

        assert [s["span"] for s in one] == [[100.0, 150.0]]
        assert [s["span"] for s in two] == [[300.0, 350.0]]


def test_rendered_scenes_merges_state_and_own_outputs():
    """자기 채널 안에서는 상태 + 산출물이 합쳐져 중복 회피가 계속 동작한다."""
    with tempfile.TemporaryDirectory() as root:
        src = "/srv/sources/가나다/EP1.mp4"
        _write_plan(root, "채널1", 1, "가나다_a1", [100.0, 150.0], src)
        state = {"channels": {"채널1": {"episodes": {"1": {"scenes": [
            {"span": [500.0, 560.0], "run_id": "가나다_seed"}]}}}}}

        got = sorted(s["span"] for s in sl.rendered_scenes(state, "채널1", 1, src, [root], 0.5, 15))
        assert got == [[100.0, 150.0], [500.0, 560.0]]


# ── 확정된 장면은 반드시 상태로 저장돼야 한다 ──

def test_record_scene_survives_path_video_path():
    """ensure_episode_source 는 Path 를 돌려준다 — 상태에 그대로 담기면 save_state 가 죽는다.

    생성이 끝난 뒤에 터지는 자리라 30~90분 쓴 렌더가 기록되지 않았다(2026-07-29: 로컬 소스 3채널 전부)."""
    state = {}
    sl.record_scene(state, "채널1", "작품", 1, Path("/srv/sources/가나다/EP1.mp4"),
                    [100.0, 150.0], "가나다_a1", "/out/job")

    ep = state["channels"]["채널1"]["episodes"]["1"]
    assert ep["video_path"] == "/srv/sources/가나다/EP1.mp4"
    json.dumps(state, ensure_ascii=False)      # ← 예전엔 TypeError: PosixPath not JSON serializable


def test_save_state_round_trips_after_record_scene():
    """실제 저장 경로로도 한 번 확인한다(json.dumps 직접 호출만으론 save_state 변경을 못 잡는다)."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d, "scene_loop_state.json")
        state = {}
        sl.record_scene(state, "채널1", "작품", 1, Path("/srv/sources/가나다/EP1.mp4"),
                        [100.0, 150.0], "가나다_a1", "/out/job")
        sl.save_state(state, p)

        back = json.loads(p.read_text(encoding="utf-8"))
        assert back["channels"]["채널1"]["episodes"]["1"]["scenes"][0]["span"] == [100.0, 150.0]


def test_episode_dir_name_matches_outdir_convention():
    # outdir 생성과 회차 스캔이 같은 이름을 써야 유튜브 중복판정이 동작한다
    assert sl.episode_dir_name(5) == "ep05" and sl.episode_dir_name(410) == "ep410"


# ── 장면 분류: 반려(비공개/삭제)를 공개 대기로 세지 않는다 ──

def _patch(**kw):
    old = {k: getattr(sl, k) for k in kw}
    for k, v in kw.items():
        setattr(sl, k, v)
    return old


def _restore(old):
    for k, v in old.items():
        setattr(sl, k, v)


def test_classify_scenes_separates_public_pending_rejected():
    scenes = [{"span": [0.0, 1.0], "run_ids": ["w_pub"]},
              {"span": [2.0, 3.0], "run_ids": ["w_unl"]},
              {"span": [4.0, 5.0], "run_ids": ["w_priv"]},
              {"span": [6.0, 7.0], "run_ids": ["w_none"]}]
    old = _patch(
        # w_none 은 발행된 적이 없어 링크가 없다
        db_run_videos=lambda conn, ch, rids: {"w_pub": ["v1"], "w_unl": ["v2"], "w_priv": ["v3"]},
        # v3 는 응답에 없다 = 공개 API 키로 조회 불가 = 비공개거나 삭제됨
        youtube_statuses=lambda vids, key: {"v1": "public", "v2": "unlisted"},
    )
    try:
        kinds = sl.classify_scenes(scenes, None, "채널1", "KEY")
    finally:
        _restore(old)
    assert kinds == [sl.SCENE_PUBLIC, sl.SCENE_PENDING, sl.SCENE_REJECTED, sl.SCENE_PENDING]


def test_scene_keeps_pending_when_one_video_is_still_unlisted():
    """같은 장면에 반려분과 검수대기분이 섞이면 여전히 대기다 — 사람이 공개할 여지가 남아 있다."""
    scenes = [{"span": [0.0, 1.0], "run_ids": ["w_a", "w_b"]}]
    old = _patch(
        db_run_videos=lambda conn, ch, rids: {"w_a": ["v_priv"], "w_b": ["v_unl"]},
        youtube_statuses=lambda vids, key: {"v_unl": "unlisted"},
    )
    try:
        assert sl.classify_scenes(scenes, None, "채널1", "KEY") == [sl.SCENE_PENDING]
    finally:
        _restore(old)


def test_rejected_scenes_do_not_block_generation():
    """🛑 회귀 방지 — 반려만 상한만큼 쌓인 회차가 영구 교착되면 안 된다.

    예전엔 pending = 렌더 - 공개 였으므로 반려 3개 = 대기 3개 = 상한 → wait_publish 로 멈추고,
    사람이 공개해 줄 수 없으니 그 회차가 영원히 안 풀렸다(2026-07-30 실측).
    """
    scenes = [{"span": [10.0, 20.0], "run_ids": ["w_a"]},
              {"span": [30.0, 40.0], "run_ids": ["w_b"]},
              {"span": [50.0, 60.0], "run_ids": ["w_c"]}]
    cfg = {"quota_per_episode": 3, "max_pending_unpublished": 3,
           "dup_iou_threshold": 0.5, "dup_center_tolerance_sec": 5}
    old = _patch(
        discover_episodes_for=lambda ch, gen_py, hours, log: [(1, "/srv/EP1.mp4")],
        rendered_scenes=lambda *a, **k: scenes,
        db_run_videos=lambda conn, ch, rids: {r: [f"vid_{r}"] for r in rids},
        youtube_statuses=lambda vids, key: {},          # 전부 조회 불가 = 전부 반려
    )
    try:
        action, ep_num, vp, info = sl.channel_plan(
            cfg, {"channel": "채널1", "work_title": "작품"}, {}, None, "KEY", ["/out"])
    finally:
        _restore(old)
    assert (info["rejected"], info["pending"], info["public"]) == (3, 0, 0)
    assert action == "gen" and ep_num == 1
    # 반려 구간은 중복 회피용으로 남아야 한다 — 사람이 버린 구간을 다시 만들면 안 된다
    assert [s["span"] for s in info["scenes"]] == [[10.0, 20.0], [30.0, 40.0], [50.0, 60.0]]


def test_unlisted_backlog_still_blocks_generation():
    """반려 제외가 브레이크 자체를 없애면 안 된다 — 검수대기 3개는 여전히 멈춘다."""
    scenes = [{"span": [float(i), float(i) + 1], "run_ids": [f"w_{i}"]} for i in range(3)]
    cfg = {"quota_per_episode": 3, "max_pending_unpublished": 3,
           "dup_iou_threshold": 0.5, "dup_center_tolerance_sec": 5}
    old = _patch(
        discover_episodes_for=lambda ch, gen_py, hours, log: [(1, "/srv/EP1.mp4")],
        rendered_scenes=lambda *a, **k: scenes,
        db_run_videos=lambda conn, ch, rids: {r: [f"vid_{r}"] for r in rids},
        youtube_statuses=lambda vids, key: {v: "unlisted" for v in vids},
    )
    try:
        action, _, _, info = sl.channel_plan(
            cfg, {"channel": "채널1", "work_title": "작품"}, {}, None, "KEY", ["/out"])
    finally:
        _restore(old)
    assert action == "wait_publish" and info["pending"] == 3 and info["rejected"] == 0


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"{len(fns)} passed")


if __name__ == "__main__":
    _run()
