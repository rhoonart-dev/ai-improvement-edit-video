"""upload_review_clips 순수 로직 단위테스트 — DB·Storage 없이 판정만 검증.
실행: python scripts/test_upload_review_clips.py  또는  pytest scripts/test_upload_review_clips.py
"""
from __future__ import annotations

import datetime as dt
import pathlib

import upload_review_clips as up

STATE = {
    "channels": {
        "몰입도둑": {"work_title": "SNL 코리아 리부트 시즌8", "episodes": {
            "1": {"scenes": [
                {"run_id": "SNL_3b", "job_dir": "outputs/scene_loop/몰입도둑/ep01/t1/SNL_3b",
                 "accepted_at": "2026-08-05T01:00:00"},
                {"run_id": "SNL_0b", "job_dir": "/abs/SNL_0b", "accepted_at": "2026-08-01T02:14:28"},
            ]}}},
        "여운 보관소": {"work_title": "샤먼: 미신전", "episodes": {   # 재배정 잔재 (배정 밖)
            "1": {"scenes": [{"run_id": "샤먼_e1", "job_dir": "x", "accepted_at": "2026-07-27"}]}}},
    }
}


def test_iter_state_scenes_marks_assignment():
    flat = up.iter_state_scenes(STATE, ["몰입도둑", "킥킥극장"])
    by = {(s[0], s[2]["run_id"]): s[3] for s in flat}
    assert by[("몰입도둑", "SNL_3b")] is True
    assert by[("여운 보관소", "샤먼_e1")] is False   # 올리면 안 됨 — 경고 대상
    assert len(flat) == 3


def test_within_days_filters_old_scenes():
    today = dt.date(2026, 8, 5)
    assert up.within_days("2026-08-05T01:00:00", 1, today) is True
    assert up.within_days("2026-08-04T23:00:00", 1, today) is False   # 어제 = 오늘 테스트 제외
    assert up.within_days("2026-07-01", None, today) is True          # 무제한 = 백로그 포함
    assert up.within_days("깨진값", 1, today) is True                  # 파싱 실패는 포함(안전)


def test_decide_covers_all_states():
    assert up.decide(None) == "ingest_upload"                 # DB 에 없음 → 적재부터
    assert up.decide((None, None)) == "upload"                # 미발행·사본 없음
    assert up.decide((None, "review-clips/m/x.mp4")) == "skip_uploaded"   # 검수 대기 중
    assert up.decide(("yt123", "review-clips/m/x.mp4")) == "cleanup"      # 발행 확인 → 정리
    assert up.decide(("yt123", None)) == "skip_published"
    # ⚠ cleanup 은 결정(review_decisions)이 아니라 **발행 사실**(video_external_id)로만 판단 —
    #   합격 기록만 있고 아직 발행 전인 사본을 지우면 검수함에서 재생이 깨진다.


def test_own_object_guard():
    assert up.own_object("review-clips/macmini-luna1/r.mp4", "macmini-luna1") is True
    assert up.own_object("review-clips/macmini-luna2/r.mp4", "macmini-luna1") is False  # 남의 사본
    assert up.own_object(None, "macmini-luna1") is False
    assert up.own_object("other-bucket/macmini-luna1/r.mp4", "macmini-luna1") is False


def test_object_path_and_job_dir():
    # 🛑 키는 clip_id(uuid) — run_id(한글)를 키에 쓰면 Storage 가 InvalidKey 로 거부한다
    #    (2026-08-05 맥1 실측. percent-encode 로도 못 푼다 — 서버가 키 문자 자체를 검사).
    assert up.object_path("macmini-luna4", "0a1b2c3d-e4f5-6789-abcd-ef0123456789") \
        == "macmini-luna4/0a1b2c3d-e4f5-6789-abcd-ef0123456789.mp4"
    assert up.object_path("m", "a/b") == "m/a_b.mp4"          # 경로 붕괴 방어
    root = "/Users/x/ves/ai-video"
    assert up.resolve_job_dir("outputs/scene_loop/a", root) == pathlib.Path(root) / "outputs/scene_loop/a"
    assert up.resolve_job_dir("/abs/path", root) == pathlib.Path("/abs/path")


def test_needs_judge_only_for_unpublished_unjudged_undecided():
    """judge 선실행 대상 = 클립 있음 · 미발행 · judge 없음 · 사람 결정 없음.

    표시용 선실행이므로: 이미 결정된 건 비용 낭비, 이미 발행된 건 구 흐름에서 judge 를
    거쳤다. 발행 여부 판단(합격/반려)에는 절대 쓰지 않는다 — 100% 사람 몫.
    """
    row = ("cid1", None, "review-clips/m/cid1.mp4")   # (clip_id, video_external_id, storage_path)
    assert up.needs_judge(row, set(), set()) is True
    assert up.needs_judge(row, {"cid1"}, set()) is False          # 이미 judge 있음
    assert up.needs_judge(row, set(), {"cid1"}) is False          # 사람이 이미 결정
    assert up.needs_judge(("cid1", "yt123", None), set(), set()) is False  # 발행됨
    assert up.needs_judge(None, set(), set()) is False            # 클립 미적재


def test_storage_request_percent_encodes_korean_path(monkeypatch):
    """🛑 회귀 방지 — run_id 는 한글이다(전 작품). 경로를 percent-encode 하지 않으면 urllib 이
    요청라인에서 UnicodeEncodeError 를 던지고, 그 예외가 스캔 전체를 죽여 그날 업로드가 통째로
    빈다(2026-08-05 맥1 실측 — '내부 오류 무시' 한 줄만 남고 담당 4채널이 전부 미업로드)."""
    seen = {}

    class _R:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        req.full_url.encode("ascii")   # urllib 이 요청라인에서 하는 검사와 동일
        return _R()

    monkeypatch.setattr(up.urllib.request, "urlopen", fake_urlopen)
    status = up.storage_request(
        "https://x.supabase.co", "KEY", "POST",
        "review-clips/macmini-luna1/원희는_스무살_54.mp4", data=b"x")
    assert status == 200
    assert "%EC" in seen["url"]                      # 한글이 인코딩됐다
    assert seen["url"].startswith("https://x.supabase.co/storage/v1/object/review-clips/")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")
    print("all passed")
