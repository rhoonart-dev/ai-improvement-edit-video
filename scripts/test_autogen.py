"""autogen.build_gen_cmd / parse_job_id 순수 함수 단위테스트.
실행: python scripts/test_autogen.py  또는  pytest scripts/test_autogen.py
"""
from __future__ import annotations

import autogen as ag


def test_build_cmd_local_video():
    cmd = ag.build_gen_cmd({"source": "/x/EP06.mp4", "work_title": "로맨스의 절댓값", "max_shorts": 1}, "py", "/wt")
    assert cmd[:4] == ["py", "-m", "app.cli", "create_shorts"]
    assert "--video" in cmd and "/x/EP06.mp4" in cmd
    assert "--youtube-url" not in cmd
    assert "--title" in cmd and "로맨스의 절댓값" in cmd
    assert "--no-research" in cmd          # 기본 빠름


def test_build_cmd_youtube_and_maxshorts():
    cmd = ag.build_gen_cmd({"source": "https://youtu.be/abc", "work_title": "W", "max_shorts": 2}, "py", "/wt")
    assert "--youtube-url" in cmd and "https://youtu.be/abc" in cmd
    assert cmd[cmd.index("--max-shorts") + 1] == "2"


def test_build_cmd_topic_episode_research_on():
    cmd = ag.build_gen_cmd({"source": "/x.mp4", "work_title": "W", "topic": "고백", "episode": 6, "max_shorts": 1},
                           "py", "/wt", no_research=False)
    assert "--topic" in cmd and "고백" in cmd
    assert cmd[cmd.index("--episode") + 1] == "6"
    assert "--no-research" not in cmd


def test_parse_job_id():
    out = "shorts_1: x\nrun_log: /U/ai-video-t0-2/outputs/로맨스의_절댓값_0b/run_log.json\n"
    assert ag.parse_job_id(out) == "로맨스의_절댓값_0b"
    assert ag.parse_job_id("no path here") is None


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"{len(fns)} passed")


if __name__ == "__main__":
    _run()
