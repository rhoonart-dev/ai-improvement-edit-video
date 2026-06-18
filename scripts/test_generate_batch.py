#!/usr/bin/env python3
"""generate_batch.build_cmd 테스트 — 양산 명령에 '고친 설정'이 항상 들어가는지.
실행: /Users/gimsewon/rhoonart/ai-video/.venv/bin/python -m pytest scripts/test_generate_batch.py -q"""
from generate_batch import build_cmd


def test_good_flags_always_present():
    cmd = build_cmd("py", {"video": "v.mp4", "subtitle": "s.srt", "title": "작품"}, 3)
    j = " ".join(cmd)
    assert "--silence-profile aggressive" in j
    assert "--length-profile tight" in j
    assert "--loudness-lufs -14" in j
    assert "--video v.mp4" in j and "--subtitle s.srt" in j and "--title 작품" in j
    assert "--max-shorts 3" in j


def test_episode_optional():
    with_ep = build_cmd("py", {"video": "v", "subtitle": "s", "title": "t", "episode": "6"}, 1)
    assert "--episode 6" in " ".join(with_ep)
    without = build_cmd("py", {"video": "v", "subtitle": "s", "title": "t"}, 1)
    assert "--episode" not in " ".join(without)


def test_outdir_optional():
    cmd = build_cmd("py", {"video": "v", "subtitle": "s", "title": "t", "outdir": "out_x"}, 2)
    assert "--outdir out_x" in " ".join(cmd)


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
