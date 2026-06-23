"""envload.load_env 단위테스트 — 주입 규칙(override 보존), 주석/따옴표 처리, 파일 없음.
실행: python scripts/test_envload.py  또는  pytest scripts/test_envload.py
"""
from __future__ import annotations

import os
import tempfile

import envload


def _write(text):
    fd, path = tempfile.mkstemp(suffix=".env")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def test_loads_basic_and_strips_quotes():
    p = _write('FOO_X=bar\nQUO_X="quoted"\nSQ_X=\'single\'\n')
    for k in ["FOO_X", "QUO_X", "SQ_X"]:
        os.environ.pop(k, None)
    loaded = envload.load_env(p)
    assert os.environ["FOO_X"] == "bar"
    assert os.environ["QUO_X"] == "quoted"
    assert os.environ["SQ_X"] == "single"
    assert loaded["FOO_X"] == "bar"
    os.unlink(p)


def test_does_not_override_existing_by_default():
    p = _write("KEEP_X=fromfile\n")
    os.environ["KEEP_X"] = "preset"
    envload.load_env(p)
    assert os.environ["KEEP_X"] == "preset"  # inline/export가 우선
    os.unlink(p)


def test_override_true_replaces():
    p = _write("OVR_X=fromfile\n")
    os.environ["OVR_X"] = "preset"
    envload.load_env(p, override=True)
    assert os.environ["OVR_X"] == "fromfile"
    os.unlink(p)


def test_skips_comments_and_blanks():
    p = _write("# comment\n\nGOOD_X=1\nnotassign\n")
    os.environ.pop("GOOD_X", None)
    loaded = envload.load_env(p)
    assert loaded == {"GOOD_X": "1"}
    os.unlink(p)


def test_missing_file_returns_empty():
    assert envload.load_env("/no/such/.env-xyz") == {}


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"{len(fns)} passed")


if __name__ == "__main__":
    _run()
