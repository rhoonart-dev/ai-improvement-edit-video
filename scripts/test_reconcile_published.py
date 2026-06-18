"""reconcile_published 순수 매칭 로직 단위테스트 (DB 무관).
실행: python scripts/test_reconcile_published.py  또는  pytest scripts/test_reconcile_published.py
"""
from __future__ import annotations

import reconcile_published as rp

# 실 youtube_studio 샘플 기반 픽스처
V_MATCH = {"content_id": "vid_math", "video_length": "40.0", "licensed_video_title": "로맨스의 절댓값",
           "video_title": "수학 4점 맞은 학생의 기상천외한 반성문? #로맨스의절대값"}
V_NEAR = {"content_id": "vid_math2", "video_length": "41.0", "licensed_video_title": "로맨스의 절댓값",
          "video_title": "수학 4점 맞은 학생의 기상천외한 반성문? #로맨스"}
V_OTHER = {"content_id": "vid_snl", "video_length": "61.0", "licensed_video_title": "SNL 코리아 리부트 시즌8",
           "video_title": "쿨한 척하는 추성훈이 딸의 남자친구를 보자마자 #snl #추성훈"}
# ai-video 클립: 2줄 제목(\n) + 작품 + 길이
CLIP = {"title": "수학 4점 맞은 학생의\n기상천외한 반성문?", "work_title": "로맨스의 절댓값", "duration_sec": 40}


def test_normalize_strips_hashtags_and_newlines():
    assert rp.normalize_title("수학 4점 맞은 학생의\n기상천외한 반성문?") == "수학 4점 맞은 학생의 기상천외한 반성문"
    assert "#" not in rp.normalize_title("수학 #로맨스의절대값")
    assert rp.normalize_title(None) == "" and rp.normalize_title("") == ""


def test_title_similarity_high_for_same_video_low_for_other():
    assert rp.title_similarity(CLIP["title"], V_MATCH["video_title"]) > 0.9
    assert rp.title_similarity(CLIP["title"], V_OTHER["video_title"]) < 0.3


def test_duration_score():
    assert rp.duration_score(40, "40.0") == 1.0
    assert rp.duration_score(40, "61.0") == 0.0       # |40-61|/4 > 1 → clamp 0
    assert rp.duration_score(None, "40.0") == 0.5
    assert rp.duration_score(40, None) == 0.5


def test_work_match():
    assert rp.work_match("로맨스의 절댓값", V_MATCH["video_title"], V_MATCH["licensed_video_title"]) is True
    assert rp.work_match("로맨스의 절댓값", V_OTHER["video_title"], V_OTHER["licensed_video_title"]) is False
    assert rp.work_match(None, "x", "y") is False


def test_rank_picks_correct_match():
    ranked = rp.rank_candidates(CLIP, [V_OTHER, V_MATCH])
    assert ranked[0]["content_id"] == "vid_math"
    assert ranked[0]["score"] > 0.9
    assert ranked[-1]["content_id"] == "vid_snl"


def test_decide_auto_when_strong_and_unique():
    dec, shown = rp.decide(rp.rank_candidates(CLIP, [V_MATCH, V_OTHER]))
    assert dec == "auto" and shown[0]["content_id"] == "vid_math"


def test_decide_ambiguous_when_two_close():
    dec, shown = rp.decide(rp.rank_candidates(CLIP, [V_MATCH, V_NEAR]))
    assert dec == "ambiguous" and len(shown) == 2


def test_decide_none_when_all_weak():
    dec, shown = rp.decide(rp.rank_candidates(CLIP, [V_OTHER]))
    assert dec == "none" and shown == []


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"{len(fns)} passed")


if __name__ == "__main__":
    _run()
