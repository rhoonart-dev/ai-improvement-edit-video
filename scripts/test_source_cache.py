"""source_cache 순수 함수 단위테스트 — 폴더 규약 · 영상ID 대조 · 캐시 판정 (네트워크 무관).
실행: python scripts/test_source_cache.py  또는  pytest scripts/test_source_cache.py
"""
from __future__ import annotations

import tempfile
import unicodedata
from pathlib import Path

import source_cache as sc


# ── 작품 폴더 이름 ──

def test_work_slug_prefers_explicit():
    assert sc.work_slug("놀라운 토요일", "nolto") == "nolto"


def test_work_slug_folds_nfd():
    # macOS 가 한글을 NFD 로 주면 눈으로 같은 폴더가 둘 생긴다
    nfd = unicodedata.normalize("NFD", "도깨비 10주년 여행")
    assert nfd != "도깨비 10주년 여행"
    assert sc.work_slug(nfd) == sc.work_slug("도깨비 10주년 여행")


def test_episode_dir_pads_number():
    d = sc.episode_dir("/srv/sources", "nolto", 7)
    assert d.as_posix().endswith("nolto/ep007")
    assert sc.episode_dir("/srv", "x", 426).name == "ep426"


# ── 영상 ID ──

def test_youtube_video_id():
    assert sc.youtube_video_id("https://www.youtube.com/watch?v=ECbzm_ha64k") == "ECbzm_ha64k"
    assert sc.youtube_video_id("https://youtu.be/abc") is None      # watch URL 만 다룬다
    assert sc.youtube_video_id(None) is None


# ── 캐시 판정 ──

def _mk(d, video=True, meta_vid=None, sub=None):
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    if video:
        (p / sc.VIDEO_NAME).write_bytes(b"x")
    if meta_vid:
        sc.write_meta(p, video_id=meta_vid)
    if sub:
        (p / sub).write_text("1\n", encoding="utf-8")
    return p


def test_cache_state_miss_when_no_video():
    with tempfile.TemporaryDirectory() as t:
        assert sc.cache_state(Path(t) / "ep001", "vid")[0] == "miss"


def test_cache_state_hit_when_video_id_matches():
    with tempfile.TemporaryDirectory() as t:
        d = _mk(Path(t) / "ep001", meta_vid="ECbzm_ha64k")
        assert sc.cache_state(d, "ECbzm_ha64k")[0] == "hit"


def test_cache_state_mismatch_blocks_wrong_episode():
    # 🛑 '여배우 은진' 사고 방지 — 같은 회차 폴더에 다른 영상이 들어 있으면 생성하면 안 된다
    with tempfile.TemporaryDirectory() as t:
        d = _mk(Path(t) / "ep001", meta_vid="OTHER_VIDEO")
        state, why = sc.cache_state(d, "ECbzm_ha64k")
        assert state == "mismatch"
        assert "OTHER_VIDEO" in why and "ECbzm_ha64k" in why


def test_cache_state_hit_without_meta_is_tolerated():
    # 옛 캐시(meta 없음)는 막지 않는다 — 다만 대조는 못 한다
    with tempfile.TemporaryDirectory() as t:
        d = _mk(Path(t) / "ep001")
        assert sc.cache_state(d, "ECbzm_ha64k")[0] == "hit"


# ── 자막 탐색 ──

def test_find_subtitle_prefers_ko():
    with tempfile.TemporaryDirectory() as t:
        d = _mk(Path(t) / "ep001", sub="source.ko.srt")
        (d / "source.en.srt").write_text("x", encoding="utf-8")
        assert sc.find_subtitle(d).name == "source.ko.srt"


def test_find_subtitle_none_when_absent():
    with tempfile.TemporaryDirectory() as t:
        assert sc.find_subtitle(_mk(Path(t) / "ep001")) is None


# ── 로컬 소스 작품은 캐시를 거치지 않는다 ──

def test_ensure_passes_through_local_path():
    got, sub = sc.ensure_episode_source(
        {"work_title": "X"}, 1, "/srv/sources/x/EP1.mp4",
        gen_py="py", ai_video_root="/ai", sources_root="/srv")
    assert str(got) == "/srv/sources/x/EP1.mp4" and sub is None


def test_ensure_raises_when_cache_missing_and_download_disabled():
    with tempfile.TemporaryDirectory() as t:
        try:
            sc.ensure_episode_source(
                {"work_title": "놀라운 토요일"}, 426,
                "https://www.youtube.com/watch?v=abc123def", gen_py="py",
                ai_video_root="/ai", sources_root=t, allow_download=False)
            raise AssertionError("캐시 없고 다운로드 금지면 예외여야 한다")
        except FileNotFoundError as e:
            assert "fetch_sources" in str(e)


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"{len(fns)} passed")


if __name__ == "__main__":
    _run()
