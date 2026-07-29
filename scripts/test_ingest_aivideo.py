"""ingest_aivideo_run 순수 로직 단위테스트 (DB 무관).
실행: python scripts/test_ingest_aivideo.py   또는   pytest scripts/test_ingest_aivideo.py
"""
from __future__ import annotations

from pathlib import Path

from ingest_aivideo_run import (
    build_rows,
    canonical_json,
    extract_provenance,
    resolve_machine_id,
    sha256_hex,
    timeline_span,
)

NOWHERE = Path("/nonexistent/run-dir")   # provenance.json / checkpoint_story.json 부재 → None
NO_GIT = "/nonexistent/ai-video"         # git_head_sha → None (결정적)

SAMPLE_EDIT_PLAN = {
    "input": {"video_path": "/x/long.mp4", "work_title": "유미의 세포들 시즌3",
              "topic": "고백 장면", "language": "ko"},
    "layout": {"canvas": "1080x1920", "top_title": "맥락\n후킹", "bottom_label": "유미의 세포들 시즌3"},
    "timeline": [
        {"role": "hook", "clip_start_sec": 10.0, "clip_end_sec": 18.0, "subtitle": "..."},
        {"role": "build", "clip_start_sec": 40.0, "clip_end_sec": 70.0, "subtitle": "..."},
        {"role": "payoff", "clip_start_sec": 120.0, "clip_end_sec": 132.0, "subtitle": "..."},
    ],
    "audio_mix": {"tts_gain_db": -3, "original_gain_db": -3, "bgm_gain_db": -20},
}
SAMPLE_RUN_LOG = {"job_id": "유미의_세포들_시즌3_a1", "input": SAMPLE_EDIT_PLAN["input"], "steps": []}


def test_canonical_json_key_order_independent():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})
    assert sha256_hex(canonical_json({"a": 1})) == sha256_hex(canonical_json({"a": 1}))
    assert len(sha256_hex("x")) == 64


def test_timeline_span_sums_segments():
    o_s, o_e, dur = timeline_span(SAMPLE_EDIT_PLAN)
    assert o_s == 10.0 and o_e == 132.0
    assert dur == (18 - 10) + (70 - 40) + (132 - 120)   # 8+30+12 = 50
    assert timeline_span({"timeline": []}) == (None, None, None)
    assert timeline_span({}) == (None, None, None)


def test_extract_provenance_incomplete_without_config():
    prov = extract_provenance(SAMPLE_RUN_LOG, NOWHERE, NO_GIT)
    assert prov["complete"] is False
    assert prov["config_hash"] is None
    assert prov["git_sha"] is None              # 폴백 git도 없음 → 결정적


def test_extract_provenance_complete_with_config():
    cfg = {"app": {"target_duration_sec": 50, "viral_score_min_threshold": 0.4},
           "design": {"aspect_ratio": "1:1"}}
    rl = dict(SAMPLE_RUN_LOG,
              provenance={"git_sha": "deadbeef", "config": cfg, "prompt_versions": {"story": "v3"}})
    prov = extract_provenance(rl, NOWHERE, NO_GIT)
    assert prov["complete"] is True
    assert prov["git_sha"] == "deadbeef"
    assert prov["config_hash"] == sha256_hex(canonical_json(cfg))    # 결정적 해시
    assert prov["prompt_versions"] == {"story": "v3"}


def test_extract_provenance_host():
    """host는 그대로 실려오고, 구 런(host 없음)은 None."""
    rl = dict(SAMPLE_RUN_LOG, provenance={"git_sha": "deadbeef", "host": "3-Mac-mini.local"})
    assert extract_provenance(rl, NOWHERE, NO_GIT)["host"] == "3-Mac-mini.local"
    assert extract_provenance(SAMPLE_RUN_LOG, NOWHERE, NO_GIT)["host"] is None


def test_host_does_not_affect_config_hash():
    """★계약: 머신이 달라도 config가 같으면 config_hash가 같아야 한다.
    (아니면 두 맥에서 만든 클립이 A/B 쌍으로 대조되지 않는다)"""
    cfg = {"app": {"target_duration_sec": 50}, "design": {"aspect_ratio": "1:1"}}
    mac3 = extract_provenance(
        dict(SAMPLE_RUN_LOG, provenance={"config": cfg, "host": "3-Mac-mini.local"}), NOWHERE, NO_GIT)
    mac4 = extract_provenance(
        dict(SAMPLE_RUN_LOG, provenance={"config": cfg, "host": "4-Mac-mini.local"}), NOWHERE, NO_GIT)
    assert mac3["host"] != mac4["host"]
    assert mac3["config_hash"] == mac4["config_hash"]


def test_resolve_machine_id_prefers_stamp():
    """SCENE_LOOP_MACHINE 으로 직접 찍힌 id가 최우선(hostname 역산보다 먼저)."""
    assert resolve_machine_id("macmini-luna2", "3-Mac-mini.local") == "macmini-luna2"


def test_resolve_machine_id_from_hostname():
    """스탬프가 없으면 배정 정본 aliases 로 역산 — 이 맥은 3-mac-mini → macmini-luna3."""
    assert resolve_machine_id(None, "3-Mac-mini.local") == "macmini-luna3"
    assert resolve_machine_id(None, "lunaleuteumaeg2ui-Macmini.local") == "macmini-luna2"


def test_resolve_machine_id_unknown_is_none():
    """모르는 머신은 추측하지 않는다 — 틀린 id 보다 빈 값이 낫다."""
    assert resolve_machine_id(None, "someone-elses-laptop.local") is None
    assert resolve_machine_id(None, None) is None
    assert resolve_machine_id(None, "") is None


def test_build_rows_maps_auto_edit_clip():
    clip, meta, prov = build_rows(
        SAMPLE_EDIT_PLAN, SAMPLE_RUN_LOG, NOWHERE,
        short_label=None, content_id=None, is_exploration=False, ai_video_root=NO_GIT,
    )
    assert clip["source"] == "auto_edit"
    assert clip["work_title"] == "유미의 세포들 시즌3"
    assert clip["duration_sec"] == 50
    assert clip["is_format_short"] is True          # 50s <= 180
    assert clip["video_external_id"] is None         # 발행 전
    assert meta["ai_video_run_id"] == "유미의_세포들_시즌3_a1"
    assert meta["ingest_source"] == "ai_video"
    assert meta["edit_plan"] is SAMPLE_EDIT_PLAN     # 타임라인 통째 보존
    assert prov["complete"] is False


def test_build_rows_with_content_id_and_exploration():
    clip, meta, _ = build_rows(
        SAMPLE_EDIT_PLAN, SAMPLE_RUN_LOG, NOWHERE,
        short_label="shorts_1", content_id="YT123", is_exploration=True, ai_video_root=NO_GIT,
    )
    assert clip["episode"] == "shorts_1"
    assert clip["video_external_id"] == "YT123"
    assert clip["is_exploration"] is True


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"{len(fns)} passed")


if __name__ == "__main__":
    _run()
