#!/usr/bin/env python3
"""Phase 0 스크립트 측 테스트 — §3-3(비교군) · §3-6(+7d 창·게이트·margin) · §4-3(R5).
실행: /Users/gimsewon/rhoonart/ai-video/.venv/bin/python -m pytest scripts/test_phase0_scripts.py -q"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


# ─────────────────────────── §3-3 비교군: 동적 ∪ 레거시 ───────────────────────────
def test_comparator_exclude_unions_extra_ids():
    """동적 조회분(extra)은 레거시 AIV_IDS·코호트와 합집합 — 교체가 아니라 합집합.
       (교체하면 source='existing'으로 적재된 구 44개가 시장에 되섞임 — 검증 blocker)"""
    from m3_aivideo_benchmark import AIV_IDS, comparator_exclude
    ex = comparator_exclude(["NEW1"], extra=["DYN1", "DYN2"])
    assert set(AIV_IDS).issubset(set(ex))
    assert {"NEW1", "DYN1", "DYN2"}.issubset(set(ex))


def test_comparator_exclude_drops_none_and_empty():
    """NULL/빈 id 가 제외 배열에 들어가면 `<> ALL` 이 전 행 NULL 평가 → 비교군 전멸.
       방어적으로 파이썬 레벨에서도 걸러야 한다."""
    from m3_aivideo_benchmark import comparator_exclude
    ex = comparator_exclude([None, "", "OK"], extra=[None, "D1"])
    assert None not in ex and "" not in ex
    assert "OK" in ex and "D1" in ex


def test_default_window_is_7():
    import m3_aivideo_benchmark as m3
    assert m3.DEFAULT_WINDOW_DAYS == 7


# ─────────────────────────── §3-6 판정: margin + 감사 역전 ───────────────────────────
def test_judge_cohort_requires_margin():
    """D3: 채택 = 코호트 백분위 − baseline > margin(0.03). 단순 pct>base 비교 금지."""
    from loop_controller import judge_cohort
    assert judge_cohort(0.30, 0.21, 0.03) == "adopt"        # +0.09 > 0.03
    assert judge_cohort(0.23, 0.21, 0.03) == "reject"       # +0.02 ≤ 0.03 (마진 미달)
    assert judge_cohort(0.10, 0.21, 0.03) == "reject"
    assert judge_cohort(None, 0.21, 0.03) is None


def test_audit_reversal_uses_per_window_baselines():
    """+14d 감사: 각 창은 **자기 창의 baseline** 과 비교(교차-창 비교 금지 — measure 가드와 동일 규칙).
       +7d 는 +7d 기준선, +14d 는 +14d 기준선으로 판정 후 두 판정의 일치 여부만 본다."""
    from loop_controller import audit_reversal
    # +7d: 0.36 vs 0.30(+7d 기준) → adopt / +14d: 0.28 vs 0.21(+14d 기준) → adopt → 역전 아님
    assert audit_reversal(0.36, 0.30, 0.28, 0.21, margin=0.03) is False
    # +14d 가 자기 기준선 대비 미달이면 역전
    assert audit_reversal(0.36, 0.30, 0.22, 0.21, margin=0.03) is True
    # 둘 다 미달이면 역전 아님
    assert audit_reversal(0.31, 0.30, 0.22, 0.21, margin=0.03) is False


def test_loop_state_default_has_window_days():
    """baseline 에 창(window) 메타가 붙어야 +7d/+14d 혼동 비교를 막을 수 있다."""
    import loop_controller as lc
    s = {"baseline_pct": 0.21, "rounds": []}
    assert lc.state_window(s) == 14      # 창 미기재 구 상태 = +14d 로 간주(레거시)
    s2 = {"baseline_pct": 0.30, "baseline_window_days": 7, "rounds": []}
    assert lc.state_window(s2) == 7


# ─────────────────────────── §3-6 커버리지 게이트 (순수부) ───────────────────────────
def test_coverage_gate_sql_uses_upload_at_and_window():
    """factory GATE_SQL 패턴 이식: upload_at(데이터 날짜) 기준 max ≥ publish + window."""
    from coverage_gate import COVERAGE_SQL
    assert "upload_at" in COVERAGE_SQL and "publish_time" in COVERAGE_SQL
    assert "%(window_days)s" in COVERAGE_SQL


def test_split_covered():
    from coverage_gate import split_covered
    rows = [{"content_id": "A", "covered": True}, {"content_id": "B", "covered": False}]
    cov, uncov = split_covered(["A", "B", "C"], rows)
    assert cov == ["A"]
    assert set(uncov) == {"B", "C"}      # 미조회(C)도 미커버 취급(보수적)


# ─────────────────────────── §4-3 R5: 발행시각 근접(≤48h) ───────────────────────────
def test_validate_publish_gap():
    from register_ab_experiment import validate_publish_gap
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    ok = validate_publish_gap(t0, t0 + timedelta(hours=47))
    assert ok == []
    errs = validate_publish_gap(t0, t0 + timedelta(hours=49))
    assert errs and "48" in errs[0]
    # 발행시각 미상 → 검증 불가 에러(발행 후 등록이 정상 흐름)
    assert validate_publish_gap(None, t0) != []


def test_pairs_within_gap_filters_m4():
    """m4 분석 시에도 R5 위반 쌍은 제외(수비 이중화)."""
    from m4_ab_analysis import pairs_within_gap
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    by_pair = {
        "p1": {"treatment": (0.5, t0), "control": (0.4, t0 + timedelta(hours=10))},
        "p2": {"treatment": (0.6, t0), "control": (0.5, t0 + timedelta(days=5))},   # 위반
        "p3": {"treatment": (0.7, None), "control": (0.6, t0)},                     # 시각 미상 → 보수적 제외
    }
    pairs, dropped = pairs_within_gap(by_pair, max_hours=48)
    assert pairs == [(0.5, 0.4)]
    assert set(dropped) == {"p2", "p3"}


# ─────────────────────────── §3-4 생성 경로 통일 ───────────────────────────
def test_autogen_attaches_good_flags_by_default():
    import autogen as ag
    from generate_batch import GOOD_FLAGS
    cmd = ag.build_gen_cmd({"source": "/x.mp4", "work_title": "W", "max_shorts": 1}, "py", "/wt")
    joined = " ".join(cmd)
    for f in GOOD_FLAGS:
        assert f in cmd, f"기본 노브 플래그 누락: {f} in {joined}"


def test_autogen_round_config_flags_override():
    import autogen as ag
    cmd = ag.build_gen_cmd({"source": "/x.mp4", "work_title": "W", "max_shorts": 1}, "py", "/wt",
                           flags=["--silence-profile", "conservative",
                                  "--length-profile", "standard", "--loudness-lufs", "off"])
    assert cmd[cmd.index("--silence-profile") + 1] == "conservative"
    assert cmd[cmd.index("--loudness-lufs") + 1] == "off"


def test_autogen_flags_none_vs_empty():
    """flags=[] 는 '노브 플래그 없이'(구 worktree 호환) — None(기본)과 구분."""
    import autogen as ag
    cmd = ag.build_gen_cmd({"source": "/x.mp4", "work_title": "W", "max_shorts": 1}, "py", "/wt",
                           flags=[])
    assert "--silence-profile" not in cmd


# ─────────────────────────── §3-5 오채널 하드 실패 ───────────────────────────
def test_token_env_name_unregistered_channel_raises():
    import publish_youtube as pub
    with pytest.raises(ValueError):
        pub.token_env_name("미등록채널")


def test_apply_baseline_keeps_history_and_audit_baseline():
    """baseline 재산출(§3-6): 판정 창(+7d) baseline + **감사 창(+14d) baseline 동시 기록**
       (audit 이 교차-창 비교를 하지 않도록) + 구 값은 history 보존(감사 추적)."""
    from recompute_baseline import apply_baseline
    s = {"baseline_pct": 0.21, "rounds": [{"round": 1}]}
    ns = apply_baseline(s, 0.34, 7, n=40, audit_pct=0.27, audit_window_days=14, audit_n=38)
    assert ns["baseline_pct"] == 0.34 and ns["baseline_window_days"] == 7
    assert ns["audit_baseline_pct"] == 0.27 and ns["audit_baseline_window_days"] == 14
    assert ns["baseline_method"] == "cohort_percentile:raw"
    assert ns["baseline_history"][0]["pct"] == 0.21
    assert ns["baseline_history"][0]["window_days"] == 14   # 미기재 구 상태 = 14d
    assert ns["rounds"] == s["rounds"]                       # 라운드 이력 보존


def test_credentials_no_generic_fallback(monkeypatch):
    """등록 채널이라도 채널별 토큰이 없으면 generic YT_REFRESH_TOKEN 으로 안 넘어간다."""
    import publish_youtube as pub
    monkeypatch.setenv("YT_CLIENT_ID", "cid")
    monkeypatch.setenv("YT_CLIENT_SECRET", "cs")
    monkeypatch.delenv("YT_REFRESH_TOKEN_STORYSUNSAK", raising=False)
    monkeypatch.setenv("YT_REFRESH_TOKEN", "generic-token")   # 있어도 무시돼야 함
    assert pub._credentials("스토리순삭") is None


# ─────────────────────────── 리뷰 확정 발견 회귀 테스트 ───────────────────────────
def test_m3_main_no_argparse_shadowing():
    """blocker 회귀방지: main() 안에서 argparse 네임스페이스가 루프 변수에 덮이면
       a.window_days 가 float 에서 조회돼 크래시. 루프 변수와 겹치는 한 글자 이름 금지."""
    import inspect
    import m3_aivideo_benchmark as m3
    src = inspect.getsource(m3.main)
    assert "args = ap.parse_args()" in src
    assert "a = ap.parse_args()" not in src


def test_etl_backfills_published_at():
    """R5 전제: ETL 이 clips.published_at NULL(ingest 선적재분)을 laeebly publish_time 으로 백필."""
    import inspect
    from etl_laeebly_to_pipeline import upsert_clips
    src = inspect.getsource(upsert_clips)
    assert "DO UPDATE" in src and "published_at" in src


def test_measure_requires_recomputed_baseline_method():
    """레거시 state(길이매칭 산식)로는 어떤 창으로도 measure 불가 — 산식 가드."""
    from loop_controller import baseline_ready
    assert baseline_ready({"baseline_pct": 0.21, "rounds": []}) is False          # 레거시
    assert baseline_ready({"baseline_pct": 0.3, "baseline_window_days": 7,
                           "baseline_method": "cohort_percentile:raw"}) is True


def test_rekey_updates_rows_holding_merged_t_keys():
    """리뷰 확정(major): 병합되는 t: 키를 이미 물고 있는 sf 행도 코드-키로 재키잉돼야 함
       (안 하면 t: eb_ip 행 DELETE 후 dangling + 모집단 분열 유지)."""
    from rekey_eb_ip import plan_sf_backfill
    ip_rows = [
        {"ip_key": "CODE1", "identification_code": "CODE1", "title": "맨 끝줄 소년 (2025)"},
        {"ip_key": "t:맨끝줄소년", "identification_code": None, "title": "맨 끝줄 소년"},
    ]
    sf = [{"shorts_id": "old1", "identification_code": None,
           "licensed_video_title": None, "ip_key": "t:맨끝줄소년"}]
    updates, _ = plan_sf_backfill(sf, ip_rows, merges={"t:맨끝줄소년": "CODE1"})
    assert updates == [{"shorts_id": "old1", "ip_key": "CODE1"}]


def test_rekey_ambiguous_norm_titles_not_merged():
    """리뷰 확정(minor): 정규화 제목 충돌(예: SNL 시즌7/시즌8 → 같은 키)이면 병합·상속 금지."""
    from rekey_eb_ip import plan_ip_merges
    ip_rows = [
        {"ip_key": "SNL7", "identification_code": "SNL7", "title": "SNL 코리아 리부트 시즌7"},
        {"ip_key": "SNL8", "identification_code": "SNL8", "title": "SNL 코리아 리부트 시즌8"},
        {"ip_key": "t:snl코리아리부트", "identification_code": None, "title": "SNL 코리아 리부트"},
    ]
    assert plan_ip_merges(ip_rows) == {}    # 충돌 → 병합 대상 제외


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
