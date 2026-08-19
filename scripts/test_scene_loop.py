"""scene_loop 순수 로직 단위테스트 — 회차 발견(로컬/유튜브)·소스 선택·명령 조립.
실행: python scripts/test_scene_loop.py  또는  pytest scripts/test_scene_loop.py
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime
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
        db_run_videos=lambda conn, ch, rids: {("w_pub", "shorts_1"): ["v1"], ("w_unl", "shorts_1"): ["v2"], ("w_priv", "shorts_1"): ["v3"]},
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
        db_run_videos=lambda conn, ch, rids: {("w_a", "shorts_1"): ["v_priv"], ("w_b", "shorts_1"): ["v_unl"]},
        youtube_statuses=lambda vids, key: {"v_unl": "unlisted"},
    )
    try:
        assert sl.classify_scenes(scenes, None, "채널1", "KEY") == [sl.SCENE_PENDING]
    finally:
        _restore(old)


def test_scheduled_publish_is_pending_not_rejected():
    """🛑 회귀 방지 — 예약 공개 대기분을 반려로 세면 안 된다.

    예약 공개(publishAt)는 공개 시각 전까지 private 이라 공개 API 키 조회에 안 나온다. 조회 불가만
    보고 반려로 판정하면, scene_publish_loop 의 **기본 동작이 예약 공개**라 브레이크가 통째로
    무력해진다(2026-07-30 실측: 숏테토칩 EP1 이 예약 1건 때문에 상한이 잘못 풀렸다).
    """
    now = datetime.fromisoformat("2026-07-30T16:00:00+09:00")
    scenes = [{"span": [0.0, 1.0], "run_ids": ["w_sched"]},   # 오늘 19:00 예약
              {"span": [2.0, 3.0], "run_ids": ["w_recent"]},  # 방금 올림(예약 시각은 미기록)
              {"span": [4.0, 5.0], "run_ids": ["w_old"]},     # 오래전 올렸는데 여태 안 보임
              {"span": [6.0, 7.0], "run_ids": ["w_none"]}]    # 발행 기록 자체가 없음
    recs = {
        "w_sched": {"scheduled_publish_at": "2026-07-30T19:00:00+09:00"},
        "w_recent": {"published_at": "2026-07-30T15:00:00"},          # naive = 로컬 tz
        "w_old": {"published_at": "2026-07-01T12:00:00+09:00"},
    }
    old = _patch(
        db_run_videos=lambda conn, ch, rids: {(r, "shorts_1"): [f"vid_{r}"] for r in rids},
        youtube_statuses=lambda vids, key: {},   # 전부 조회 불가
    )
    try:
        kinds = sl.classify_scenes(scenes, None, "채널1", "KEY",
                                   publish_records=recs, now=now, grace_days=7)
    finally:
        _restore(old)
    assert kinds == [sl.SCENE_PENDING,    # 예약 대기 — 시각이 되면 스스로 공개된다
                     sl.SCENE_PENDING,    # 방금 올림 — 유예 안
                     sl.SCENE_REJECTED,   # 유예 지나도 안 보임 = 사람이 비공개로 돌렸다
                     sl.SCENE_REJECTED]   # 발행 기록 없음 = 손으로 올렸다 내린 것


def test_owner_lookup_separates_scheduled_from_rejected_without_grace():
    """★ 채널 OAuth 조회면 유예가 필요 없다 — publishAt 유무로 예약/반려가 갈린다.

    공개 API 키 경로는 private 을 아예 못 봐서 '방금 반려한 것'도 유예(기본 7일) 동안 대기로
    잡혔고, 그동안 그 회차가 상한에 걸려 생성이 멈췄다. 소유자 자격으로 보면 그날 밤 바로 풀린다.
    """
    now = datetime.fromisoformat("2026-08-04T20:00:00+09:00")
    scenes = [{"span": [0.0, 1.0], "run_ids": ["w_pub"]},
              {"span": [2.0, 3.0], "run_ids": ["w_sched"]},   # private + 예약 시각
              {"span": [4.0, 5.0], "run_ids": ["w_rej"]},     # private + 예약 없음 = 반려
              {"span": [6.0, 7.0], "run_ids": ["w_unl"]},     # unlisted = 검수 대기
              {"span": [8.0, 9.0], "run_ids": ["w_gone"]}]    # 조회 자체가 안 됨 = 삭제
    owner = {"vid_w_pub": ("public", None),
             "vid_w_sched": ("private", "2026-08-04T10:00:00Z"),
             "vid_w_rej": ("private", None),
             "vid_w_unl": ("unlisted", None)}
    old = _patch(
        db_run_videos=lambda conn, ch, rids: {(r, "shorts_1"): [f"vid_{r}"] for r in rids},
        youtube_statuses_owner=lambda vids, ch: owner,
        # 폴백이 끼어들면 안 된다 — 끼어들면 유예 규칙이 적용돼 결과가 달라진다
        youtube_statuses=lambda vids, key: (_ for _ in ()).throw(AssertionError("폴백 금지")),
    )
    try:
        # 발행 기록을 통째로 비워도(=유예 판정 근거 없음) 결과가 같아야 한다
        kinds = sl.classify_scenes(scenes, None, "채널1", "KEY", publish_records={}, now=now)
    finally:
        _restore(old)
    assert kinds == [sl.SCENE_PUBLIC, sl.SCENE_PENDING, sl.SCENE_REJECTED,
                     sl.SCENE_PENDING, sl.SCENE_REJECTED]


def test_owner_lookup_falls_back_to_public_key_on_failure():
    """🛑 토큰 만료·scope 축소로 OAuth 가 죽어도 그 채널이 통째로 스킵되면 안 된다.

    폴백은 구 경로와 똑같이 동작할 뿐이다 — 정확도(유예 필요)만 낮아진다.
    """
    now = datetime.fromisoformat("2026-08-04T20:00:00+09:00")
    scenes = [{"span": [0.0, 1.0], "run_ids": ["w_recent"]},   # 방금 올림 → 유예 안 = 대기
              {"span": [2.0, 3.0], "run_ids": ["w_old"]}]      # 유예 지남 = 반려
    def _boom(vids, ch):
        raise RuntimeError("invalid_grant")
    old = _patch(
        db_run_videos=lambda conn, ch, rids: {(r, "shorts_1"): [f"vid_{r}"] for r in rids},
        youtube_statuses_owner=_boom,
        youtube_statuses=lambda vids, key: {},   # 공개 키로는 private 이 안 보인다
    )
    try:
        kinds = sl.classify_scenes(
            scenes, None, "채널1", "KEY", now=now, grace_days=7,
            publish_records={"w_recent": {"published_at": "2026-08-04T19:00:00"},
                             "w_old": {"published_at": "2026-07-01T12:00:00+09:00"}})
    finally:
        _restore(old)
    assert kinds == [sl.SCENE_PENDING, sl.SCENE_REJECTED]


def test_explicit_rejected_at_beats_lookup_but_not_actual_public():
    """사람이 남긴 명시적 반려가 추론보다 우선한다. 단 실제로 공개돼 있으면 그게 사실이다."""
    scenes = [{"span": [0.0, 1.0], "run_ids": ["w_marked"]},   # unlisted 인데 반려 표시
              {"span": [2.0, 3.0], "run_ids": ["w_public"]}]   # 공개인데 반려 표시
    owner = {"vid_w_marked": ("unlisted", None), "vid_w_public": ("public", None)}
    old = _patch(
        db_run_videos=lambda conn, ch, rids: {(r, "shorts_1"): [f"vid_{r}"] for r in rids},
        youtube_statuses_owner=lambda vids, ch: owner,
    )
    try:
        kinds = sl.classify_scenes(
            scenes, None, "채널1", "KEY",
            publish_records={"w_marked": {"rejected_at": "2026-08-04T19:37:00"},
                             "w_public": {"rejected_at": "2026-08-04T19:37:00"}})
    finally:
        _restore(old)
    assert kinds == [sl.SCENE_REJECTED, sl.SCENE_PUBLIC]


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
        db_run_videos=lambda conn, ch, rids: {(r, "shorts_1"): [f"vid_{r}"] for r in rids},
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
        db_run_videos=lambda conn, ch, rids: {(r, "shorts_1"): [f"vid_{r}"] for r in rids},
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


# ─────── 서수 회차 · 제외 규칙 · 슬롯 (2026-07-30) ───────

def test_exclude_entries_filters_title_and_alt_titles():
    """alt_titles 도 봐야 한다 — 회차는 옛 한글 제목으로 살아나는데 제외는 못 걸리면
    권리 범위 밖 영상이 소스가 된다."""
    E = [{"id": "a", "title": "웃긴거ㅣB급 청문회 EP.01"},
         {"id": "b", "title": "B-Class Hearing EP.02", "alt_titles": ["B급 청문회 EP.02"]},
         {"id": "c", "title": "입만살아서 ep.01"}]
    assert [e["id"] for e in sl.exclude_entries(E, "청문회")] == ["c"]
    assert sl.exclude_entries(E, None) == E and sl.exclude_entries(E, "  ") == E


def test_ordinal_episodes_numbers_oldest_first():
    E = [{"id": "new", "title": "3번째", "duration": 900},      # flat 목록은 최신순
         {"id": "mid", "title": "2번째", "duration": 900},
         {"id": "old", "title": "1번째", "duration": 900}]
    assert {n: c[0]["id"] for n, c in sl.ordinal_episodes(E).items()} == {1: "old", 2: "mid", 3: "new"}


def test_ordinal_episodes_applies_duration_before_numbering():
    """하한 미달분을 번호 부여 뒤에 빼면 회차 번호에 구멍이 난다."""
    E = [{"id": "new", "title": "x", "duration": 900},
         {"id": "short", "title": "y", "duration": 60},
         {"id": "old", "title": "z", "duration": 900}]
    assert {n: c[0]["id"] for n, c in sl.ordinal_episodes(E, 1, 300).items()} == {1: "old", 2: "new"}


def test_exclusion_applies_before_ordinal_numbering():
    E = [{"id": "new", "title": "입만살아서 ep.02", "duration": 900},
         {"id": "skip", "title": "B급 청문회 EP.99", "duration": 900},
         {"id": "old", "title": "입만살아서 ep.01", "duration": 900}]
    idx = sl.ordinal_episodes(sl.exclude_entries(E, "청문회"), 1, 300)
    assert {n: c[0]["id"] for n, c in idx.items()} == {1: "old", 2: "new"}


def test_ordinal_start_episode_trims_front():
    E = [{"id": f"v{i}", "title": str(i), "duration": 900} for i in range(5, 0, -1)]
    assert sorted(sl.ordinal_episodes(E, 3)) == [3, 4, 5]


def test_slot_key_defaults_to_channel():
    assert sl.slot_key({"channel": "B급 순삭"}) == "B급 순삭"


def test_slot_key_separates_two_works_on_one_channel():
    a = {"channel": "재미쇼츠", "slot": "재미쇼츠·유미의 세포들 시즌3"}
    b = {"channel": "재미쇼츠", "slot": "재미쇼츠·언더커버셰프"}
    assert sl.slot_key(a) != sl.slot_key(b)      # EP1 끼리 섞이지 않는다


def test_save_gen_output_writes_full_stdout_and_stderr():
    """실패 원인은 대개 stderr 꼬리 300자 밖(재시도 WARN 은 stdout)이라 전문을 남긴다."""
    with tempfile.TemporaryDirectory() as outdir:
        path = sl.save_gen_output(outdir, ["py", "-m", "app.cli"], 1,
                                  "    [WARN] 응답이 잘렸습니다 재시도 중...", "Traceback …")
        body = Path(path).read_text(encoding="utf-8")
        assert Path(path).name == "gen_output.log"
        assert "rc=1" in body
        assert "[WARN] 응답이 잘렸습니다" in body      # stdout 이 보존된다
        assert "Traceback" in body


def test_save_gen_output_accepts_bytes_and_none():
    """타임아웃 경로는 파이썬 판마다 bytes/None 으로 온다 — 진단 저장이 거기서 죽으면 안 된다."""
    with tempfile.TemporaryDirectory() as outdir:
        path = sl.save_gen_output(outdir, ["py"], "timeout", b"\xed\x95\x9c\xea\xb8\x80", None)
        assert "한글" in Path(path).read_text(encoding="utf-8")


def test_dedup_spans_excludes_production_rejects():
    """0009: 제작 반려(reject_type=production) 구간만 회피에서 빠진다 — 장면 반려·무결정은 유지."""
    import scene_loop as sl
    scenes = [
        {"span": [10, 60], "run_ids": ["r_prod"]},     # 제작 반려 → 재시도 허용(회피 제외)
        {"span": [100, 150], "run_ids": ["r_scene"]},  # 장면 반려 → 회피 유지
        {"span": [200, 250], "run_ids": ["r_none"]},   # 결정 없음 → 회피 유지
        {"span": [300, 350], "run_ids": ["r_prod", "r_scene"]},  # 혼합(재렌더 겹침) → 보수적으로 유지
    ]
    recs = {"r_prod": {"reject_type": "production", "rejected_at": "t"},
            "r_scene": {"reject_type": "scene", "rejected_at": "t"}}
    spans = sl.dedup_spans(scenes, recs)
    assert [10, 60] not in spans
    assert [100, 150] in spans and [200, 250] in spans and [300, 350] in spans
    assert sl.dedup_spans(scenes, None) == [s["span"] for s in scenes]  # 기록 없으면 전부 유지


# ── 한 job 의 여러 테이크(--max-shorts N) 처리 ──
# 첫 테이크가 기존 장면과 겹치면 예전엔 전 과정을 재생성했다(2026-08-05 리와인드포차 3시간).
# 이제 같은 분석이 낸 변이 중 안 겹치는 것을 골라 쓰고, 하류가 아는 이름으로 올린다.

def _job_with_takes(tmp, spans):
    """spans[i] → 테이크 i 의 edit_plan/mp4 를 만든다(0=정본, 1.. =변이)."""
    job = Path(tmp, "job")
    job.mkdir(parents=True, exist_ok=True)
    for i, (s, e) in enumerate(spans):
        plan = {"timeline": [{"clip_start_sec": s, "clip_end_sec": e}]}
        name = "edit_plan.json" if i == 0 else f"edit_plan_{i + 1}.json"
        vid = "shorts.mp4" if i == 0 else f"shorts_{i + 1}.mp4"
        Path(job, name).write_text(json.dumps(plan), encoding="utf-8")
        Path(job, vid).write_text(f"take{i}", encoding="utf-8")
    return str(job)


def test_job_takes_orders_canonical_first():
    with tempfile.TemporaryDirectory() as d:
        job = _job_with_takes(d, [(10, 20), (30, 40), (50, 60)])
        assert [t[0] for t in sl.job_takes(job)] == ["shorts", "shorts_2", "shorts_3"]


def test_take_label_maps_canonical_to_ingest_key():
    # 정본만 파일명(shorts.mp4)과 멱등 키(shorts_1)가 어긋난다 — 여기서 통일한다
    assert sl.take_label("shorts") == "shorts_1"
    assert sl.take_label("shorts_2") == "shorts_2"


def test_take_files_resolves_video_and_plan():
    with tempfile.TemporaryDirectory() as d:
        job = _job_with_takes(d, [(10, 20), (30, 40)])
        v, plan = sl.take_files(job, "shorts_1")
        assert v.name == "shorts.mp4" and plan.name == "edit_plan.json"
        v2, plan2 = sl.take_files(job, "shorts_2")
        assert v2.read_text() == "take1" and json.loads(plan2.read_text())["timeline"][0]["clip_start_sec"] == 30


def test_record_scene_stores_take():
    state = {}
    sl.record_scene(state, "채널1", "작품", 1, "/src.mp4", [10.0, 20.0], "job_a", "/out", take="shorts_2")
    assert state["channels"]["채널1"]["episodes"]["1"]["scenes"][0]["take"] == "shorts_2"


def test_build_cmd_passes_max_shorts():
    cmd = sl.build_cmd("py", "작품", "/src.mp4", "/out", [], ep_num=1, max_shorts=3)
    assert cmd[cmd.index("--max-shorts") + 1] == "3"


# ── 실패 재개 (--from-step) ──
# 생성 시간의 대부분은 청크 분석이다. 뒤 단계에서 죽어도 처음부터 다시 돌리던 탓에
# 2026-08-06 하루에 70분·40분을 날렸다 — 둘 다 checkpoint_gemini.json 이 남아 있었다.

def test_resume_point_prefers_gemini_checkpoint():
    with tempfile.TemporaryDirectory() as d:
        job = Path(d, "피의_게임_X_ab"); job.mkdir()
        Path(job, "checkpoint_probe.json").write_text("{}", encoding="utf-8")
        Path(job, "checkpoint_gemini.json").write_text("{}", encoding="utf-8")
        assert sl.resume_point(str(job)) == ("피의_게임_X_ab", "graph")


def test_resume_point_falls_back_to_gemini_step():
    with tempfile.TemporaryDirectory() as d:
        job = Path(d, "job_x"); job.mkdir()
        Path(job, "checkpoint_probe.json").write_text("{}", encoding="utf-8")
        assert sl.resume_point(str(job)) == ("job_x", "gemini")


def test_resume_point_none_without_checkpoints():
    with tempfile.TemporaryDirectory() as d:
        job = Path(d, "job_y"); job.mkdir()
        assert sl.resume_point(str(job)) is None
        assert sl.resume_point(None) is None


def test_any_job_dir_finds_failed_job_without_edit_plan():
    # 실패한 job 은 edit_plan 이 없다 — newest_job_dir 로는 못 찾는다
    with tempfile.TemporaryDirectory() as d:
        job = Path(d, "job_z"); job.mkdir()
        Path(job, "checkpoint_gemini.json").write_text("{}", encoding="utf-8")
        assert sl.newest_job_dir(d) is None
        assert sl.any_job_dir(d) == str(job)


def test_build_cmd_resume_args():
    cmd = sl.build_cmd("py", "작품", "/src.mp4", "/out", [], ep_num=2,
                       from_step="graph", job_id="job_ab")
    assert cmd[cmd.index("--from-step") + 1] == "graph"
    assert cmd[cmd.index("--job-id") + 1] == "job_ab"
    # 재개가 아니면 인자가 아예 없어야 한다(빈 값으로 넘기면 ai-video 가 --job-id 요구로 죽는다)
    assert "--from-step" not in sl.build_cmd("py", "작품", "/src.mp4", "/out", [], ep_num=2)


def test_takes_of_one_run_are_classified_independently():
    """🛑 회귀 방지 — 한 job 의 테이크 3편이 서로의 발행 상태를 물려받으면 안 된다.

    2026-08-06 `--max-shorts 3` 전환으로 run_id 하나에 장면이 3개 달리게 됐다. run_id 로만
    묶으면 테이크1 이 공개되는 순간 테이크2·3 도 '공개됨'이 돼 **회차가 조기 종료**되고,
    검수 대기분이 통계에서 사라진다.
    """
    scenes = sl.merge_scenes([{"span": [0.0, 1.0], "run_id": "R", "take": "shorts_1"},
                              {"span": [200.0, 260.0], "run_id": "R", "take": "shorts_2"},
                              {"span": [400.0, 460.0], "run_id": "R", "take": "shorts_3"}], 0.5, 15)
    assert [s["keys"] for s in scenes] == [["R"], ["R#shorts_2"], ["R#shorts_3"]]
    old = _patch(
        db_run_videos=lambda conn, ch, rids: {("R", "shorts_1"): ["v1"], ("R", "shorts_2"): ["v2"]},
        youtube_statuses=lambda vids, key: {"v1": "public", "v2": "unlisted"},
    )
    try:
        kinds = sl.classify_scenes(scenes, None, "채널1", "KEY")
    finally:
        _restore(old)
    # 테이크3 은 아직 업로드조차 안 됐다(링크 없음) → 대기
    assert kinds == [sl.SCENE_PUBLIC, sl.SCENE_PENDING, sl.SCENE_PENDING]


def test_reject_and_dedup_records_are_read_per_take():
    """반려·제작반려 기록도 테이크 자리에서만 읽힌다 — 남의 테이크를 막거나 풀면 안 된다."""
    scenes = sl.merge_scenes([{"span": [0.0, 1.0], "run_id": "R", "take": "shorts_1"},
                              {"span": [200.0, 260.0], "run_id": "R", "take": "shorts_2"}], 0.5, 15)
    recs = {"R#shorts_2": {"rejected_at": "2026-08-06T00:00:00", "reject_type": "production"}}
    old = _patch(
        db_run_videos=lambda conn, ch, rids: {("R", "shorts_1"): ["v1"], ("R", "shorts_2"): ["v2"]},
        youtube_statuses=lambda vids, key: {"v1": "public"},
    )
    try:
        kinds = sl.classify_scenes(scenes, None, "채널1", "KEY", publish_records=recs)
    finally:
        _restore(old)
    assert kinds == [sl.SCENE_PUBLIC, sl.SCENE_REJECTED]
    # 제작반려는 중복 회피에서 빠지고(0009), 정본 구간은 그대로 남는다
    assert sl.dedup_spans(scenes, recs) == [[0.0, 1.0]]


def test_scene_keys_accepts_legacy_scene_shape():
    """옛 상태 파일로 만든 장면({'run_ids'} 만 있음)도 그대로 동작해야 한다."""
    assert sl.scene_keys({"run_ids": ["X", "Y"]}) == ["X", "Y"]
    assert sl.scene_keys({"run_id": "Z"}) == ["Z"]


# ── run_id 충돌 가드 (2026-08 맥4·맥2 실측) ──

def _state_with(run_id, span, take="shorts_1", slot="채널1", ep="1"):
    state = {}
    sl.record_scene(state, slot, "작품", ep, "/srv/EP1.mp4", span, run_id, "/out/job", take=take)
    return state


def test_run_id_conflicts_flags_same_id_with_different_scene():
    """같은 run_id 인데 구간이 다르다 = 다른 장면이 같은 키를 받았다(접미 충돌)."""
    state = _state_with("가나다_a1", [2520.0, 2707.0])
    hits = sl.run_id_conflicts(state, {}, "가나다_a1", "shorts_1", [1185.0, 1230.0], 0.5, 15)
    assert hits and "다른 장면" in hits[0]


def test_run_id_conflicts_allows_rerender_of_same_scene():
    """재개·재렌더로 같은 장면을 다시 적는 것은 정상 — 막으면 안 된다."""
    state = _state_with("가나다_a1", [2520.0, 2707.0])
    assert sl.run_id_conflicts(state, {}, "가나다_a1", "shorts_1", [2521.0, 2706.0], 0.5, 15) == []


def test_run_id_conflicts_flags_existing_publish_record():
    """새로 만든 장면이 이미 발행·반려 기록을 가지고 있을 수는 없다."""
    recs = {"가나다_a1": {"published_at": "2026-08-04T00:00:00"}}
    assert sl.run_id_conflicts({}, recs, "가나다_a1", "shorts_1", [10.0, 40.0], 0.5, 15)
    # 테이크가 다르면 키도 달라 무관하다
    assert sl.run_id_conflicts({}, recs, "가나다_a1", "shorts_2", [10.0, 40.0], 0.5, 15) == []


def test_run_id_conflicts_ignores_other_take_in_state():
    """한 job 의 테이크들은 run_id 를 공유한다 — 테이크가 다르면 충돌이 아니다."""
    state = _state_with("가나다_a1", [2520.0, 2707.0], take="shorts_2")
    assert sl.run_id_conflicts(state, {}, "가나다_a1", "shorts_1", [1185.0, 1230.0], 0.5, 15) == []


def test_quota_of_prefers_work_card_over_global_policy():
    """작품 카드의 quota 가 정책 전역값을 덮어야 한다 — 한 작품만 늘리려는 것이므로."""
    cfg = {"quota_per_episode": 3}
    assert sl.quota_of(cfg, {"channel": "다람쥐 숏토리"}) == 3
    assert sl.quota_of(cfg, {"channel": "한 입 주막", "quota_per_episode": 10}) == 10


def test_channel_plan_uses_work_quota_for_completion():
    """공개 3개는 전역 quota 로는 완료지만 작품 quota 10 에서는 계속 생성해야 한다."""
    scenes = [{"span": [float(i) * 10, float(i) * 10 + 5], "run_ids": [f"w_{i}"]} for i in range(3)]
    cfg = {"quota_per_episode": 3, "max_pending_unpublished": 3,
           "dup_iou_threshold": 0.5, "dup_center_tolerance_sec": 5}
    old = _patch(
        discover_episodes_for=lambda ch, gen_py, hours, log: [(1, "/srv/EP1.mp4")],
        rendered_scenes=lambda *a, **k: scenes,
        db_run_videos=lambda conn, ch, rids: {(r, "shorts_1"): [f"vid_{r}"] for r in rids},
        youtube_statuses=lambda vids, key: {v: "public" for v in vids},
    )
    try:
        ch = {"channel": "한 입 주막", "work_title": "가왕쇼"}
        assert sl.channel_plan(cfg, dict(ch), {}, None, "KEY", ["/out"])[0] == "done_all"
        action, ep_num, _, info = sl.channel_plan(
            cfg, dict(ch, quota_per_episode=10), {}, None, "KEY", ["/out"])
    finally:
        _restore(old)
    assert (action, ep_num, info["public"]) == ("gen", 1, 3)
