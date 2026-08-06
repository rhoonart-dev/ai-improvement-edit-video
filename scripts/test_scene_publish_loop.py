"""scene_publish_loop 단위테스트 — 중복 업로드 가드와 공개 슬롯 배분.
실행: python scripts/test_scene_publish_loop.py  또는  pytest scripts/test_scene_publish_loop.py
"""
from __future__ import annotations

from datetime import datetime

import scene_loop as sl
import scene_publish_loop as pl


class _StubConn:
    def close(self):
        pass


def _patch(mod, **kw):
    old = {k: getattr(mod, k) for k in kw}
    for k, v in kw.items():
        setattr(mod, k, v)
    return old


def _restore(mod, old):
    for k, v in old.items():
        setattr(mod, k, v)


def _gen_state():
    return {"channels": {"채널1": {"work_title": "작품", "episodes": {"1": {"scenes": [
        {"run_id": "작품_pub", "span": [0.0, 1.0], "job_dir": "/out/a", "accepted_at": "2026-07-01T00:00:00"},
        {"run_id": "작품_new", "span": [2.0, 3.0], "job_dir": "/out/b", "accepted_at": "2026-07-02T00:00:00"},
    ]}}}}}


def test_db_linked_scene_is_not_republished():
    """🛑 회귀 방지 — 사람이 손으로 발행한 분을 다시 올리면 안 된다.

    pub_state 는 이 루프를 붙이기 전 이력을 모른다. 진실은 DB 링크다.
    2026-07-30 이식 당시 첫 dry-run 이 18건을 미발행으로 집었고 그중 3건은 이미 공개된 영상이었다.
    """
    pub_state = {"scenes": {}}
    old_sl = _patch(sl,
                    _connect_db=lambda log: _StubConn(),
                    db_run_videos=lambda conn, ch, rids: {"작품_pub": ["vidAAA"]})
    try:
        assert pl.seed_published_from_db(_gen_state(), pub_state, lambda m: None) is True
    finally:
        _restore(sl, old_sl)

    assert pub_state["scenes"]["작품_pub"]["stage"] == "published"
    assert pub_state["scenes"]["작품_pub"]["video_id"] == "vidAAA"
    todo = pl.pending_scenes(_gen_state(), pub_state)
    assert [t[3]["run_id"] for t in todo] == ["작품_new"]


def test_publish_stops_when_db_unavailable():
    """DB 연결이 안 되면 정합을 못 하므로 발행을 시작하면 안 된다(호출부가 종료한다)."""
    old_sl = _patch(sl, _connect_db=lambda log: None, db_run_videos=lambda *a: {})
    try:
        assert pl.seed_published_from_db(_gen_state(), {"scenes": {}}, lambda m: None) is False
    finally:
        _restore(sl, old_sl)


def test_next_publish_slot_advances_per_day():
    """하루 1슬롯이면 이미 잡힌 날은 건너뛰고 다음 날로 넘어간다."""
    now = datetime.fromisoformat("2026-07-30T09:00:00+09:00")
    cfg = {"publish_times": ["19:00"]}
    empty = {"scenes": {}}
    first = pl.next_publish_slot(cfg, empty, "채널1", now=now)
    assert first.date().isoformat() == "2026-07-30" and first.hour == 19

    taken = {"scenes": {"r1": {"channel": "채널1", "scheduled_publish_at": first.isoformat()}}}
    second = pl.next_publish_slot(cfg, taken, "채널1", now=now)
    assert second.date().isoformat() == "2026-07-31" and second.hour == 19


def test_next_publish_slot_fills_today_when_late():
    """오늘 몫이 비었는데 슬롯 시각만 지났으면 내일로 밀지 않고 지금+5분(사실상 즉시 공개).

    2026-08-05 운영자 결정 — 종전 동작(무조건 내일로)은 저녁 검수분이 하루를 통째로
    잃었다. 오늘 몫이 이미 잡혀 있으면 종전대로 내일 슬롯이다(하루 상한 유지)."""
    from datetime import timedelta
    now = datetime.fromisoformat("2026-07-30T21:00:00+09:00")
    slot = pl.next_publish_slot({"publish_times": ["19:00"]}, {"scenes": {}}, "채널1", now=now)
    assert slot == now + timedelta(minutes=5)

    taken = {"scenes": {"r1": {"channel": "채널1",
                               "scheduled_publish_at": "2026-07-30T19:00:00+09:00"}}}
    slot2 = pl.next_publish_slot({"publish_times": ["19:00"]}, taken, "채널1", now=now)
    assert slot2.date().isoformat() == "2026-07-31" and slot2.hour == 19


def test_publish_config_channel_record_overrides_policy():
    """전역 정책을 채널 레코드가 덮는다 — 채널마다 공개 속도가 다를 수 있다."""
    merged = pl.publish_config({"channel": "채널1"},
                               {"publish_times": ["19:00"]},
                               [{"name": "채널1", "publish_times": ["12:00", "20:00"]}])
    assert merged["publish_times"] == ["12:00", "20:00"]


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"{len(fns)} passed")


if __name__ == "__main__":
    _run()


def test_review_gate_routes_all_cases():
    """4단계(2026-08-05): 합격작만 발행. 결정 없음=보류, 반려=해제 — 발행은 approved 뿐이다."""
    import scene_publish_loop as spl
    assert pl.review_gate(None) == pl.REVIEW_GATE_HOLD                       # 미검수 → 보류
    assert pl.review_gate(("approved", "2026-08-05T00:00:00", None)) == pl.REVIEW_GATE_PUBLISH
    assert pl.review_gate(("rejected", "2026-08-05T00:00:00", "TTS 안 맞음")) == pl.REVIEW_GATE_REJECT


# ── 테이크별 검수 결정·상태 키 ──
# 변이 테이크가 합격해도 조회에 안 잡혀 영영 보류되던 문제(2026-08-06 명장면 세탁소 shorts_2).

def test_state_key_keeps_canonical_backward_compatible():
    assert pl.state_key("피의_게임_X_1e") == "피의_게임_X_1e"
    assert pl.state_key("피의_게임_X_1e", "shorts_1") == "피의_게임_X_1e"
    # 변이는 분리 — 같은 run_id 의 테이크들이 서로의 발행 기록을 덮어쓰면 안 된다
    assert pl.state_key("피의_게임_X_1e", "shorts_2") == "피의_게임_X_1e#shorts_2"


def test_take_files_picks_variant_video():
    v, plan = pl.take_files("/job", "shorts_2")
    assert v.name == "shorts_2.mp4" and plan.name == "edit_plan_2.json"
    v1, plan1 = pl.take_files("/job", "shorts_1")
    assert v1.name == "shorts.mp4" and plan1.name == "edit_plan.json"


def test_find_video_does_not_fall_back_for_variants(tmp_path):
    # 변이 파일이 없는데 정본으로 폴백하면 **다른 영상이 발행된다**
    (tmp_path / "shorts.mp4").write_text("canonical")
    assert pl.find_video(str(tmp_path), "shorts_2") is None
    assert pl.find_video(str(tmp_path), "shorts_1").endswith("shorts.mp4")
