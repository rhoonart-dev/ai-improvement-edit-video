"""check_assignments 순수 판정 단위테스트 — 미지 키·소스 범위·정규식 앵커·중복 배정·구멍 감지.
실행: python scripts/test_check_assignments.py  또는  pytest scripts/test_check_assignments.py
"""
from __future__ import annotations

import check_assignments as ck

CH = [
    {"name": "숏테토칩", "works": ["도깨비 10주년 여행"], "geoblock_capable": False},
    {"name": "재미쇼츠", "works": ["유미의 세포들 시즌3"], "geoblock_capable": True},
    {"name": "다람쥐 숏토리", "works": ["언더커버셰프"], "geoblock_capable": False},
]

PLAYLIST_CARD = {
    "source": {"type": "youtube_playlist", "url": "https://www.youtube.com/playlist?list=PLx",
               "episode_regex": r"\bEP[.\s]?(\d{1,3})\b", "start_episode": 1,
               "min_source_duration_sec": 600},
    "constraints": {"geoblock_required": False, "subtitles": "none"},
    "_guide": "플레이리스트 한정",
}


# ── 소스 범위(type) ↔ URL 모양 ──

def test_url_matches_type_guards_rights_scope():
    # 🛑 '해당 플레이리스트 영상만 사용 가능' 작품에 채널 URL 을 넣으면 채널 전체가 소스가 된다
    assert ck.url_matches_type("youtube_playlist", "https://www.youtube.com/playlist?list=PLx")
    assert not ck.url_matches_type("youtube_playlist", "https://www.youtube.com/@tvNJoy/videos")
    assert ck.url_matches_type("youtube_channel", "https://www.youtube.com/@tvNJoy/videos")
    assert not ck.url_matches_type("youtube_channel", "https://www.youtube.com/playlist?list=PLx")


# ── 정규식 ──

def test_regex_problem_detects_missing_group_and_empty():
    assert ck.regex_problem(None)
    assert ck.regex_problem(r"\bEP\d+\b")          # 캡처그룹 없음
    assert ck.regex_problem(r"EP(") is not None    # 컴파일 실패
    assert ck.regex_problem(r"\bEP[.\s]?(\d{1,3})\b") is None


def test_has_work_anchor():
    # 채널 전체가 소스면 EP 표기만으로는 다른 작품 3화를 집는다 → 작품 리터럴이 있어야 한다
    assert not ck.has_work_anchor(r"\bEP[.\s]?(\d{1,3})\b")
    assert ck.has_work_anchor(r"#스트릿레스토랑파이터\s*EP[.\s]?(\d{1,3})\b")


# ── 미지 키 (오타로 설정이 조용히 사라지는 것 방지) ──

def test_unknown_keys():
    assert ck.unknown_keys({"type": "local", "min_source_duration_secs": 600}, ck.SOURCE_KEYS) \
        == ["min_source_duration_secs"]
    assert ck.unknown_keys({"type": "local"}, ck.SOURCE_KEYS) == []


def test_branding_problem():
    # 정상 — 로고만 있어도 되고(전역 기본 박스), 예외값을 줄 수도 있다
    assert ck.branding_problem({"logo": "RZsv4.png"}) is None
    assert ck.branding_problem({"logo": "lt0JP.png", "box": "262x280", "align": "center",
                                "_note": "트림본"}) is None
    # 🛑 오타 키는 로고 설정을 통째로 조용히 무시하게 만든다
    assert ck.branding_problem({"logo": "a.png", "size": "262x280"})
    # 🛑 box 형식 오류는 생성 subprocess 에서 예외로 죽는다(_parse_box) → 그날 채널이 빠진다
    assert ck.branding_problem({"logo": "a.png", "box": "262*280"})
    assert ck.branding_problem({"logo": "a.png", "align": "bottom"})
    assert ck.branding_problem({"box": "262x280"})          # logo 없이 크기만
    assert ck.branding_problem("RZsv4.png")                 # 객체가 아님


def test_is_nfc():
    import unicodedata
    assert ck.is_nfc("도깨비 10주년 여행")
    assert not ck.is_nfc(unicodedata.normalize("NFD", "도깨비 10주년 여행"))


# ── 하한 스모크: 구멍만 잡는다 ──

def _e(dur, title):
    return {"duration": dur, "title": title}


def test_duration_smoke_flags_middle_hole():
    # 도깨비 EP3 패턴 — 앞뒤는 살아 있는데 가운데가 비었다
    entries = [_e(900, "EP.1"), _e(300, "EP.2"), _e(900, "EP.3")]
    keep, holes = ck.duration_smoke(entries, r"\bEP[.\s]?(\d{1,3})\b", 600)
    assert keep == 2 and holes == [2]


def test_duration_smoke_ignores_trailing_preview():
    # 아직 방영 전이라 예고편만 있는 마지막 회차는 정상 — 매번 경고하면 사람이 무시하게 된다
    entries = [_e(900, "EP.1"), _e(900, "EP.2"), _e(64, "[7화 예고] EP.7")]
    keep, holes = ck.duration_smoke(entries, r"\bEP[.\s]?(\d{1,3})\b", 600)
    assert keep == 2 and holes == []


def test_duration_smoke_uses_alt_titles():
    # 유튜브가 영어 제목을 주면 EP 표기가 잘린다 → 과거에 본 한글 제목으로 살아남아야 한다
    entries = [{"duration": 1187, "title": "English truncated ...",
                "alt_titles": ["한글 제목 … EP.3"]}]
    keep, holes = ck.duration_smoke(entries, r"\bEP[.\s]?(\d{1,3})\b", 600)
    assert keep == 1 and holes == []


# ── 오프라인 통합 판정 ──

def _run_offline(**kw):
    rep = ck.Report()
    base = dict(records=CH, works={"도깨비 10주년 여행": PLAYLIST_CARD},
                assignments={"machines": {"m": {"channels": ["숏테토칩"]}}}, notice={})
    base.update(kw)
    ck.check_offline(rep, **base)
    return rep


def test_offline_clean_config_has_no_blocks():
    assert _run_offline().counts()[0] == 0


def test_offline_detects_duplicate_channel_assignment():
    rep = _run_offline(assignments={"machines": {
        "a": {"channels": ["숏테토칩"]}, "b": {"channels": ["숏테토칩"]}}})
    assert rep.counts()[0] >= 1
    assert any("두 곳에 배정" in m for _, m in rep.rows)


def test_offline_detects_missing_channel_in_registry():
    rep = _run_offline(assignments={"machines": {"m": {"channels": ["없는채널"]}}})
    assert any("channels.json 에 없습니다" in m for _, m in rep.rows)


def test_offline_detects_missing_work_card():
    rep = _run_offline(works={})
    assert any("카드가 config/works.json 에 없습니다" in m for _, m in rep.rows)


def test_offline_detects_scope_mismatch():
    bad = {**PLAYLIST_CARD, "source": {**PLAYLIST_CARD["source"],
                                       "url": "https://www.youtube.com/@x/videos"}}
    rep = _run_offline(works={"도깨비 10주년 여행": bad})
    assert any("권리 범위를 벗어날 수 있습니다" in m for _, m in rep.rows)


def test_offline_requires_subtitles_and_guide():
    bad = {**PLAYLIST_CARD, "constraints": {"geoblock_required": False}}
    assert any("subtitles" in m for _, m in _run_offline(works={"도깨비 10주년 여행": bad}).rows)
    bad2 = {k: v for k, v in PLAYLIST_CARD.items() if k != "_guide"}
    assert any("_guide" in m for _, m in _run_offline(works={"도깨비 10주년 여행": bad2}).rows)


def test_offline_detects_notice_key_drift_but_allows_uncarded_work():
    # 카드가 아예 없는 작품의 표기 설정은 정상(아직 카드가 없을 뿐)
    assert _run_offline(notice={"유미의 세포들 시즌3": {}}).counts()[0] == 0
    # 카드와 글자만 다른 키는 조용히 무시되므로 차단
    rep = _run_offline(notice={"도깨비10주년여행": {}})
    assert any("글자가 다릅니다" in m for _, m in rep.rows)


def test_offline_detects_alias_collision():
    rep = _run_offline(assignments={"machines": {
        "a": {"aliases": {"hostname": ["shared"]}, "channels": ["숏테토칩"]},
        "b": {"aliases": {"hostname": ["shared"]}, "channels": ["재미쇼츠"]}}},
        works={"도깨비 10주년 여행": PLAYLIST_CARD,
               "유미의 세포들 시즌3": PLAYLIST_CARD})
    assert any("자동 감지가 어느 쪽인지" in m for _, m in rep.rows)


# ── branding(로고) 카드 검증 ──
# 로고 배선(2026-07-29)을 넣을 때 CARD_KEYS 갱신을 빠뜨려 이 검증기가 ⛔ 를 냈고,
# scene_loop_run.sh 가 종료코드로 생성을 중단시켜 밤 루프가 통째로 막혔다. 회귀 방지.

def _brand_card(branding):
    card = dict(PLAYLIST_CARD)
    card["branding"] = branding
    return card


def _brand_blocks(branding):
    rep = _run_offline(works={"도깨비 10주년 여행": _brand_card(branding)})
    return [m for lv, m in rep.rows if "branding" in m]


def test_valid_branding_passes():
    assert _brand_blocks({"logo": "RZsv4.png"}) == []
    assert _brand_blocks({"logo": "RZsv4.png", "box": "395x280", "align": "center"}) == []


def test_unknown_branding_key_blocks():
    # 오타로 box→bx 가 되면 그 작품만 전역 기본 크기로 조용히 나간다
    assert _brand_blocks({"logo": "a.png", "bx": "395x280"})


def test_branding_without_logo_blocks():
    # 해석 계층이 logo 유무로만 판단하므로 logo 없는 branding 은 아무 효과가 없다
    assert _brand_blocks({"box": "395x280"})



def test_bad_box_format_blocks():
    assert _brand_blocks({"logo": "a.png", "box": "395-280"})
    assert _brand_blocks({"logo": "a.png", "box": "395x"})


def test_bad_align_blocks():
    assert _brand_blocks({"logo": "a.png", "align": "middle"})


# ── 미등록 채널의 차단 범위 ──
# 새 머신은 배정을 먼저 적고 channels.json 을 나중에 채우는 순서로 붙는다(맥6·2026-07-29).
# 그동안 무관한 머신까지 멈추면 온보딩이 불가능해지므로, 남의 미등록 채널은 참고로만 낸다.

def _missing_channel(scope):
    rep = ck.Report()
    ck.check_offline(rep, records=CH, works={"도깨비 10주년 여행": PLAYLIST_CARD},
                     assignments={"machines": {
                         "me": {"channels": ["숏테토칩"]},
                         "other": {"channels": ["아직없는채널"]}}},
                     notice={}, scope_machine=scope)
    return rep


def test_other_machine_missing_channel_does_not_block():
    rep = _missing_channel("me")
    assert rep.counts()[0] == 0                                   # ⛔ 0 → 이 머신은 돈다
    assert any("아직 돌 수 없다" in m for _, m in rep.rows)


def test_own_missing_channel_still_blocks():
    rep = _missing_channel("other")
    assert rep.counts()[0] >= 1
    assert any("channels.json 에 없습니다" in m for _, m in rep.rows)


# ★ 스크립트 실행 진입점은 **파일 맨 끝**에 둔다 — _run() 이 globals() 를 훑어 테스트를 모으므로,
# 이 블록 뒤에 정의된 테스트는 스크립트 모드에서 조용히 빠진다(머지로 뒤에 붙은 branding·범위
# 테스트 8건이 실제로 그 상태였다. pytest 는 수집하므로 아무도 몰랐다).

def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"{len(fns)} passed")


if __name__ == "__main__":
    _run()
