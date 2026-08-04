"""send_heartbeat 파싱 단위테스트 — 로그 샘플은 2026-08-03 맥1 실측 발췌.
실행: python scripts/test_send_heartbeat.py  또는  pytest scripts/test_send_heartbeat.py
"""
from __future__ import annotations

import send_heartbeat as hb

LOG = """===== 2026-08-02 00:00:01 배정 검증 =====
=== 루프 운영 정본 검증 === (범위: macmini-luna1)
  ※ 채널 'まいにちじゃんまんるぴー'(macmini-luna6) 가 config/channels.json 에 없음 — 그 머신은 아직 돌 수 없다
⛔ 0건 · ⚠️ 0건
===== 2026-08-02 00:00:02 scene_loop 시작 =====
[다람쥐 숏토리 · 원희는 스무살] EP1: 공개 0/3 (렌더 1, 미공개 1) → 이번에 1장면 생성 (소스 HelloTwenty.mp4)
[다람쥐 숏토리 · 원희는 스무살]   시도 1/3: python -m app.cli create_shorts …
[다람쥐 숏토리 · 원희는 스무살]   ✓ 새 장면 확정(미공개) [1158.1, 1215.0] (run=원희는_스무살_7b) — 공개 0/3 유지
===== 2026-08-02 02:58:16 scene_loop 종료 (rc=0) =====
===== 2026-08-03 00:00:02 scene_loop 시작 =====
[다람쥐 숏토리 · 원희는 스무살] EP1: 공개 0/3, 미공개 대기 3개(상한 3) → 생성 멈춤. 발행/공개 필요
[몰입도둑 · SNL 코리아 리부트 시즌8] EP1: 공개 0/3 (렌더 2, 미공개 2) → 이번에 1장면 생성 (소스 SNL.mp4)
[몰입도둑 · SNL 코리아 리부트 시즌8]   시도 1/3: python -m app.cli create_shorts …
[몰입도둑 · SNL 코리아 리부트 시즌8]   ↻ 중복 장면 [3274.0, 3510.6] (기존과 겹침) → 재생성
[몰입도둑 · SNL 코리아 리부트 시즌8]   시도 2/3: python -m app.cli create_shorts …
[몰입도둑 · SNL 코리아 리부트 시즌8]   ✗ 생성 실패 rc=1 → 이 채널 오늘 종료. 전문: outputs/scene_loop/몰입도둑/ep01/try2_20260803_013005/gen_output.log
      stderr꼬리: json.decoder.JSONDecodeError: Extra data: line 581 column 1 (char 17417)
[락커룸 · 국대: 로드 투 노스 아메리카] EP1: 공개 0/3, 미공개 대기 3개(상한 3) → 생성 멈춤. 발행/공개 필요
=== scene_loop 종료 ===
===== 2026-08-03 02:31:29 scene_loop 종료 (rc=0) =====
"""


def test_last_run_segment_takes_last_block():
    seg, started = hb.last_run_segment(LOG)
    assert started == "2026-08-03 00:00:02"
    assert "몰입도둑" in seg and "원희는_스무살_7b" not in seg  # 이전 실행 구간은 제외


def test_parse_channels_results():
    seg, _ = hb.last_run_segment(LOG)
    by = {e["channel"]: e for e in hb.parse_channels(seg)}
    assert by["다람쥐 숏토리"]["result"] == "paused_pending"
    assert by["다람쥐 숏토리"]["pending"] == 3
    assert by["락커룸"]["result"] == "paused_pending"
    m = by["몰입도둑"]
    assert m["result"] == "failed"
    assert m["tries"] == 2                      # ↻ 재생성 후 2차 시도
    assert m["episode"] == 1 and m["quota"] == 3
    assert m["gen_log"].endswith("gen_output.log")
    assert m["error_class"] == "llm_json"        # stderr꼬리의 JSONDecodeError 로 분류


def test_generated_run_id_from_earlier_block():
    # 이전(8/2) 구간을 직접 파싱하면 generated + run_id
    first = LOG.split("===== 2026-08-03")[0]
    seg = first[first.index("===== 2026-08-02 00:00:02"):]
    by = {e["channel"]: e for e in hb.parse_channels(seg)}
    assert by["다람쥐 숏토리"]["result"] == "generated"
    assert by["다람쥐 숏토리"]["run_id"] == "원희는_스무살_7b"


def test_warnings_from_last_validation_block():
    warns = hb.parse_warnings(LOG)
    assert len(warns) == 1 and "まいにち" in warns[0]


def test_classify_error_table():
    assert hb.classify_error("json.decoder.JSONDecodeError: Expecting ','") == "llm_json"
    assert hb.classify_error("google 429 RESOURCE_EXHAUSTED") == "api_quota"
    assert hb.classify_error("FileNotFoundError: [Errno 2] No such file or directory: '/Users/x/.venv/bin/python'") == "env_config"
    assert hb.classify_error("TypeError: Object of type PosixPath is not JSON serializable") == "code_bug"
    assert hb.classify_error("전혀 새로운 실패") == "unknown"


def test_gen_stage_marker():
    tail = "[7/15] 전사...\n[8/15] Gemini 분석 진행 중...\n  청크 2/12 분석 중"
    assert hb.gen_stage(tail).startswith("8/15 Gemini 분석")


def test_kst_to_utc():
    assert hb.kst_to_utc_iso("2026-08-03 09:00:00").startswith("2026-08-03T00:00:00")
    assert hb.kst_to_utc_iso("깨진값") is None




def test_build_upsert_casts():
    row = {"host": "h1", "run_started_at": "2026-08-04T00:00:00+00:00", "status": "done",
           "channels": [{"channel": "몰입도둑"}], "schema_version": 1}
    sql, vals = hb._build_upsert(row)
    assert "run_started_at" in sql and "%s::timestamptz" in sql   # psycopg v3 서버측 바인딩 대응
    assert "%s::jsonb" in sql
    assert "ON CONFLICT (host, run_started_at)" in sql
    assert "host = EXCLUDED" not in sql                            # 키 컬럼은 갱신 대상 아님
    assert vals[sql.split("(")[1].split(")")[0].split(", ").index("channels")].startswith("[")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")
    print("all passed")
