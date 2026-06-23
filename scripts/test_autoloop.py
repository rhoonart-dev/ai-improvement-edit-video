"""autoloop.discover_new / _channel_for 단위테스트 (DB 무관, temp run 디렉토리).
실행: python scripts/test_autoloop.py  또는  pytest scripts/test_autoloop.py
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import autoloop as al


def _mkrun(base, name, job_id, work):
    d = Path(base) / name
    d.mkdir(parents=True)
    (d / "edit_plan.json").write_text(json.dumps({"input": {"work_title": work}}), encoding="utf-8")
    (d / "run_log.json").write_text(json.dumps({"job_id": job_id}), encoding="utf-8")
    return d


def test_discover_new_skips_done():
    with tempfile.TemporaryDirectory() as t:
        _mkrun(t, "jobA", "A_1", "로맨스의 절댓값")
        _mkrun(t, "jobB", "B_2", "유미의 세포들 시즌3")
        new = al.discover_new(t, {"A_1"})           # A_1은 이미 처리됨
        assert {jid for _, jid in new} == {"B_2"}


def test_discover_requires_runlog():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t) / "jobC"
        d.mkdir()
        (d / "edit_plan.json").write_text("{}", encoding="utf-8")   # run_log 없음
        assert al.discover_new(t, set()) == []


def test_channel_for_maps_work():
    with tempfile.TemporaryDirectory() as t:
        d1 = _mkrun(t, "jobD", "D_1", "로맨스의 절댓값")
        d2 = _mkrun(t, "jobE", "E_1", "유미의 세포들 시즌3")
        assert al._channel_for(str(d1)) == "스토리순삭"
        assert al._channel_for(str(d2)) == "재미쇼츠"


def test_channel_for_unknown():
    with tempfile.TemporaryDirectory() as t:
        d = _mkrun(t, "jobF", "F_1", "무관한 작품")
        assert al._channel_for(str(d)) is None


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"{len(fns)} passed")


if __name__ == "__main__":
    _run()
