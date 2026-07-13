#!/usr/bin/env python
"""Phase 0 (INTEGRATION_PLAN §3-1·§3-2) 테스트 — ip_key 모집단 통일 + 에코챔버 차단.

DB 없이 순수 로직만: IPRegistry(가짜 pipe) · compute_scores(합성 rows) · retrieve(합성 shorts).
"""
from datetime import datetime, timedelta, timezone

import pytest

from cluster import IPRegistry, ip_key_for
from config import OUR_CHANNEL_NAMES
from db import Laeebly
from make_report import retrieve
from scoring import compute_scores


# ─────────────────────────── 헬퍼 ───────────────────────────
class FakePipe:
    """IPRegistry 가 쓰는 select/upsert 만 흉내."""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.upserts = []

    def select(self, table, params=None):
        return list(self.rows)

    def upsert(self, table, rows, on_conflict=None):
        self.upserts.extend(rows)


def _mk_registry(rows):
    return IPRegistry(FakePipe(rows), key="", model="m")


LICENSED_ROW = {
    "ip_key": "CODE123", "identification_code": "CODE123",
    "title": "맨 끝줄 소년 (2025)", "cluster_id": "드라마×연속서사",
    "tone_tags": ["휴먼"], "is_laeebly_licensed": True, "classify_status": "confirmed",
}


# ─────────────────────────── §3-1② 제목 역인덱스 ───────────────────────────
def test_resolve_licensed_returns_5tuple_with_ip_key():
    reg = _mk_registry([LICENSED_ROW])
    out = reg.resolve({"identification_code": "CODE123"})
    assert len(out) == 5
    cluster, tone, lic, has_src, ip_key = out
    assert cluster == "드라마×연속서사" and lic is True and has_src is True
    assert ip_key == "CODE123"


def test_finalize_unlicensed_inherits_code_key_via_title_index():
    """비라이선스 식별 결과의 제목이 코드-키 행과 정규화 일치 → t: 행 안 만들고 상속."""
    reg = _mk_registry([LICENSED_ROW])
    cls = {"has_source_video": True, "source_title": "맨 끝줄 소년",
           "format_axis": "드라마", "narrative_axis": "연속서사",
           "cluster_id": "드라마×연속서사", "tone_tags": [], "confidence": 0.9,
           "classify_status": "confirmed", "classify_model": "m"}
    cluster, tone, lic, has_src, ip_key = reg._finalize_unlicensed(cls)
    assert ip_key == "CODE123"          # t: 키가 아니라 코드-키 상속
    assert lic is True                   # 원작이 라이선스 작품
    assert not any(r["ip_key"].startswith("t:") for r in reg.pipe.upserts)


def test_finalize_unlicensed_new_title_creates_t_key():
    reg = _mk_registry([LICENSED_ROW])
    cls = {"has_source_video": True, "source_title": "완전히 새로운 작품",
           "format_axis": "예능", "narrative_axis": "에피소드완결",
           "cluster_id": "예능×에피소드완결", "tone_tags": [], "confidence": 0.9,
           "classify_status": "confirmed", "classify_model": "m"}
    *_, ip_key = reg._finalize_unlicensed(cls)
    assert ip_key == ip_key_for(None, "완전히 새로운 작품")
    assert ip_key.startswith("t:")


def test_title_index_updates_on_register_within_run():
    """같은 run 에서 라이선스 행이 새로 등록된 직후 비라이선스가 와도 상속(§3-1② 스냅샷 한계 수리)."""
    reg = _mk_registry([])
    reg._register("NEWCODE", {"ip_key": "NEWCODE", "identification_code": "NEWCODE",
                              "title": "신작 드라마", "cluster_id": "드라마×연속서사",
                              "tone_tags": [], "is_laeebly_licensed": True})
    cls = {"has_source_video": True, "source_title": "신작 드라마 (2026)",
           "format_axis": "드라마", "narrative_axis": "연속서사",
           "cluster_id": "드라마×연속서사", "tone_tags": [], "confidence": 0.9,
           "classify_status": "confirmed", "classify_model": "m"}
    *_, ip_key = reg._finalize_unlicensed(cls)
    assert ip_key == "NEWCODE"


def test_self_made_returns_none_ip_key():
    reg = _mk_registry([])
    cluster, tone, lic, has_src, ip_key = reg._finalize_unlicensed(
        {"has_source_video": False, "source_title": None})
    assert has_src is False and ip_key is None and cluster is None


def test_title_index_ambiguous_collision_no_inherit():
    """리뷰 확정: 같은 정규화 제목에 서로 다른 코드(예: SNL 시즌7/시즌8 — _norm_title 이
       시즌 표기를 지움)면 역인덱스 상속 금지 — 임의 코드 오상속 대신 t: 키로 안전 폴백."""
    snl7 = {"ip_key": "SNL7", "identification_code": "SNL7",
            "title": "SNL 코리아 리부트 시즌7", "cluster_id": "예능×에피소드완결",
            "tone_tags": [], "is_laeebly_licensed": True, "classify_status": "confirmed"}
    snl8 = {**snl7, "ip_key": "SNL8", "identification_code": "SNL8",
            "title": "SNL 코리아 리부트 시즌8"}
    reg = _mk_registry([snl7, snl8])
    cls = {"has_source_video": True, "source_title": "SNL 코리아 리부트",
           "format_axis": "예능", "narrative_axis": "에피소드완결",
           "cluster_id": "예능×에피소드완결", "tone_tags": [], "confidence": 0.9,
           "classify_status": "confirmed", "classify_model": "m"}
    *_, ip_key = reg._finalize_unlicensed(cls)
    assert ip_key not in ("SNL7", "SNL8")     # 임의 상속 금지
    assert ip_key.startswith("t:")            # 안전 폴백


# ─────────────────────────── §3-1① CONTROL_SQL 계약 ───────────────────────────
def test_control_sql_recovers_identification_code_from_title_join():
    """제목-연결(lateral) 행에서 v.identification_code 를 회수하되, 제목이 여러 원작과
       매치되면(n_match>1) 회수 포기 — 임의 코드 오귀속 방지.
       (SQL 문자열 계약 테스트 — 실 DB 불필요, 이 수리의 회귀 방지용.)"""
    sql = Laeebly.CONTROL_SQL
    assert "coalesce(ys.identification_code," in sql
    assert "when lv.n_match = 1 then lv.identification_code" in sql
    assert "count(*) over () as n_match" in sql
    assert "order by v.identification_code" in sql     # limit 1 결정성


# ─────────────────────────── §3-1③ scoring: ip_key 우선 ───────────────────────────
def _rows_same_work_split_keys():
    """같은 원작인데 키가 갈린 두 그룹: 시장(코드) 5 + 자사(제목만·ip_key로 통일) 1."""
    pt0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(5):
        rows.append({
            "shorts_id": f"mkt{i}", "channel_id": "chM", "cluster_id": "드라마×연속서사",
            "identification_code": "CODE123", "licensed_video_title": None,
            "ip_key": "CODE123", "origin": "market",
            "publish_time": (pt0 + timedelta(days=i)).isoformat(),
            "views": 1000 + i * 100, "kept_watching_rate": None, "likes": None,
            "shares": None, "comments_added": None, "lift_views": None,
            "performance_score": None,
        })
    rows.append({
        "shorts_id": "ours0", "channel_id": "chO", "cluster_id": "드라마×연속서사",
        "identification_code": None, "licensed_video_title": "맨 끝줄 소년",
        "ip_key": "CODE123", "origin": "ours",
        "publish_time": (pt0 + timedelta(days=10)).isoformat(),
        "views": 1200, "kept_watching_rate": None, "likes": None,
        "shares": None, "comments_added": None, "lift_views": None,
        "performance_score": None,
    })
    return rows


def test_scoring_uses_ip_key_to_unify_population():
    """ip_key 가 있으면 코드/제목 대신 ip_key 로 원작 모집단을 묶는다."""
    rows = _rows_same_work_split_keys()
    # POP_MIN_N=10 을 못 넘으면 원작 단이 무의미하므로 mutual(asof=False)로 전체 상호 채점
    out = compute_scores(rows, baseline_rows=[], asof=False)
    assert len(out) == len(rows)
    # ip 키 분열이 없다는 간접 증거: ours0 도 채점됨(원작 모집단 → 글로벌 폴백 어디서든)
    ours = next(o for o in out if o["shorts_id"] == "ours0")
    assert ours["performance_score"] is not None


def test_scoring_excludes_ours_from_populations():
    """origin='ours' 행은 채점 대상이되, 시장 모집단(백분위 분모)에는 안 들어간다."""
    rows = _rows_same_work_split_keys()
    out_with = compute_scores(rows, baseline_rows=[], asof=False)
    out_market_only = compute_scores([r for r in rows if r["origin"] != "ours"],
                                     baseline_rows=[], asof=False)
    # ours 를 넣고 빼도 시장 클립들의 점수가 동일해야 함(모집단 오염 0)
    with_map = {o["shorts_id"]: o["performance_score"] for o in out_with}
    only_map = {o["shorts_id"]: o["performance_score"] for o in out_market_only}
    for sid in only_map:
        assert with_map[sid] == only_map[sid], f"{sid}: ours 가 시장 모집단을 오염"


# ─────────────────────────── §3-2 retrieve: ours 하드 제외 ───────────────────────────
def _short(sid, cluster, label, score, origin="market", vlm=True):
    return {"shorts_id": sid, "cluster_id": cluster, "perf_label": label,
            "performance_score": score, "lifecycle_status": "active",
            "origin": origin, "vlm_model": "g" if vlm else None,
            "hook_0_3s": {"salience": 0.5}}


def test_retrieve_excludes_ours_everywhere():
    shorts = [
        _short("m1", "드라마×연속서사", "good", 90),
        _short("m2", "드라마×연속서사", "bad", 10),
        _short("o1", "드라마×연속서사", "good", 99, origin="ours"),   # 자사 — 제외돼야
        _short("g1", "예능×에피소드완결", "good", 95),                 # 글로벌 폴백 후보
        _short("g2", "예능×에피소드완결", "good", 94, origin="ours"),  # 자사 — 폴백에서도 제외
    ]
    ret = retrieve(shorts, "드라마×연속서사")
    got_ids = {s["shorts_id"] for s in ret["goods"]} \
        | {s["shorts_id"] for s in ret["fallback"]} \
        | ({ret["explore"]["shorts_id"]} if ret["explore"] else set())
    if ret["pair"]:
        got_ids |= {ret["pair"][0]["shorts_id"], ret["pair"][1]["shorts_id"]}
    assert "o1" not in got_ids and "g2" not in got_ids


def test_retrieve_holdout_cluster_returns_no_injection():
    shorts = [_short("m1", "드라마×연속서사", "good", 90)]
    ret = retrieve(shorts, "드라마×연속서사", holdout={"드라마×연속서사"})
    assert ret["holdout"] is True
    assert ret["goods"] == [] and ret["fallback"] == [] and ret["explore"] is None


def test_our_channel_names_constant():
    assert "재미쇼츠" in OUR_CHANNEL_NAMES and "스토리순삭" in OUR_CHANNEL_NAMES


# ─────────────────────────── §3-2 적재 시 origin 세팅 ───────────────────────────
def test_clip_origin():
    from run_factory import clip_origin
    assert clip_origin({"channel_name": "재미쇼츠", "channel_id": "UCx"}) == "ours"
    assert clip_origin({"channel_name": "스토리순삭"}) == "ours"
    assert clip_origin({"channel_name": "남의채널", "channel_id": "UCy"}) == "market"
    assert clip_origin({"channel_id": "UCy"}) == "market"       # 이름 없어도 id 있으면 시장
    assert clip_origin(None) is None                            # control 조회 실패 → 미확정
    assert clip_origin({}) is None


def test_our_channel_ids_from_rows():
    from run_factory import our_channel_ids
    rows = [
        {"channel_id": "UC_ours", "channel_name": "재미쇼츠", "origin": "ours"},
        {"channel_id": "UC_mkt", "channel_name": "남의채널", "origin": "market"},
        {"channel_id": "UC_ours2", "channel_name": "스토리순삭", "origin": None},  # 백필 전
        {"channel_id": None, "channel_name": "재미쇼츠"},
    ]
    assert our_channel_ids(rows) == {"UC_ours", "UC_ours2"}
