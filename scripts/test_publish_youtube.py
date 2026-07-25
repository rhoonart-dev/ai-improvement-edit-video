"""publish_youtube 순수/게이트 단위테스트 (build_snippet · token_env_name · gate_ok 안전 게이트).
실행: python scripts/test_publish_youtube.py  또는  pytest scripts/test_publish_youtube.py
"""
from __future__ import annotations

import os

import publish_youtube as pub


def test_snippet_newline_and_tags():
    s = pub.build_snippet("줄1\n줄2", ["#로맨스", "코미디"])
    assert s["title"] == "줄1 줄2"
    assert s["tags"] == ["로맨스", "코미디"]
    assert s["description"] == "#로맨스 #코미디"
    assert s["categoryId"] == pub.CATEGORY_ENTERTAINMENT


def test_snippet_hashtag_spaces_to_underscore():
    # 공백 포함 작품명 해시태그 → 언더바 치환 (공백이 있으면 YouTube가 태그를 끊어버림)
    s = pub.build_snippet("제목", ["도깨비 10주년 여행", "#언니네 산지직송 in 칼라페"])
    assert s["description"] == "#도깨비_10주년_여행 #언니네_산지직송_in_칼라페"
    # tags 는 원문 유지 (YouTube tags 는 공백 허용)
    assert s["tags"] == ["도깨비 10주년 여행", "언니네 산지직송 in 칼라페"]


def test_snippet_hashtag_strips_special_chars():
    # 콜론이 남으면 YouTube가 '#샤먼'까지만 태그로 인식 → 특수문자 제거, 공백은 언더바 유지
    assert pub.hashtag_body("샤먼: 미신전") == "샤먼_미신전"
    assert pub.hashtag_body("놀라운 토요일") == "놀라운_토요일"
    s = pub.build_snippet("제목", ["샤먼: 미신전"])
    assert s["description"] == "#샤먼_미신전"
    assert s["tags"] == ["샤먼: 미신전"]  # tags 원문은 보존(YouTube tags는 특수문자 허용)


def test_snippet_episode_line():
    # 설명란 = "<작품명> N화" + 빈 줄 + 해시태그
    s = pub.build_snippet("여고괴담은 다 맞혔는데", ["놀라운 토요일"],
                          work_title="놀라운 토요일", episode=425)
    assert s["description"] == "놀라운 토요일 425화\n\n#놀라운_토요일"


def test_snippet_episode_falls_back_to_first_hashtag():
    s = pub.build_snippet("제목", ["샤먼: 미신전"], episode=1)
    assert s["description"] == "샤먼: 미신전 1화\n\n#샤먼_미신전"


def test_snippet_without_episode_keeps_legacy_shape():
    # 회차 미상(큐 미경유 런)이면 기존과 동일하게 해시태그 줄만
    s = pub.build_snippet("제목", ["놀라운 토요일"], episode=None)
    assert s["description"] == "#놀라운_토요일"


def test_parse_hashtags():
    assert pub.parse_hashtags("#o483K") == ["o483K"]
    assert pub.parse_hashtags("#o483K #예능") == ["o483K", "예능"]
    assert pub.parse_hashtags("o483K, eMQvA") == ["o483K", "eMQvA"]
    assert pub.parse_hashtags(None) == [] and pub.parse_hashtags("  ") == []


def _lv(title, code, company="CJ ENM"):
    """licensed_video 행 흉내 — (title, required_hashtags_description, identification_code, company)"""
    return (title, f"#{code}", code, company)


def test_pick_licensed_row_cjenm_prefers_g():
    # CJ ENM 이면 '(g)' 변형 코드를 써야 한다(실측: 놀라운 토요일 o483K / (g) CCGDN)
    rows = [_lv("놀라운 토요일", "o483K"), _lv("놀라운 토요일 (g)", "CCGDN")]
    assert pub.pick_licensed_row(rows, "놀라운 토요일")[2] == "CCGDN"


def test_pick_licensed_row_non_cjenm_keeps_base():
    rows = [_lv("어떤 작품", "AAA11", company="다른배급사"),
            _lv("어떤 작품 (g)", "BBB22", company="다른배급사")]
    assert pub.pick_licensed_row(rows, "어떤 작품")[2] == "AAA11"


def test_pick_licensed_row_cjenm_without_g_uses_base():
    rows = [_lv("샤먼: 미신전", "eMQvA")]
    assert pub.pick_licensed_row(rows, "샤먼: 미신전")[2] == "eMQvA"


def test_pick_licensed_row_missing():
    assert pub.pick_licensed_row([], "없는 작품") is None
    # 기본행 없이 (g)만 있으면 그거라도 쓴다
    assert pub.pick_licensed_row([_lv("작품 (g)", "ZZZ99")], "작품")[2] == "ZZZ99"


def test_snippet_work_code_hashtag():
    # 식별코드는 해시태그 줄 끝에 붙되 YouTube tags 에는 안 들어간다
    s = pub.build_snippet("여고괴담은 다 맞혔는데", ["놀라운 토요일"],
                          work_title="놀라운 토요일", episode=425, work_hashtags=["o483K"])
    assert s["description"] == "놀라운 토요일 425화\n\n#놀라운_토요일 #o483K"
    assert s["tags"] == ["놀라운 토요일"]


def test_snippet_work_code_dedup():
    # 이미 같은 해시태그가 있으면 중복 추가하지 않는다
    s = pub.build_snippet("제목", ["o483K"], work_hashtags=["#o483K"])
    assert s["description"] == "#o483K"


def test_snippet_work_code_without_episode():
    s = pub.build_snippet("제목", ["샤먼: 미신전"], work_hashtags=["eMQvA"])
    assert s["description"] == "#샤먼_미신전 #eMQvA"


def test_snippet_title_max_100():
    assert len(pub.build_snippet("가" * 150)["title"]) == 100


def test_snippet_empty():
    s = pub.build_snippet("", None)
    assert s["title"] == "shorts" and s["tags"] == [] and s["description"] == ""


def test_token_env_name():
    assert pub.token_env_name("이불 속 극장") == "YT_REFRESH_TOKEN_CINEMAINBED"
    assert pub.token_env_name("재미쇼츠") == "YT_REFRESH_TOKEN_JAEMISHOTS"
    # §3-5 오채널 업로드 차단: 미등록 채널 → generic 폴백 금지, 하드 실패
    try:
        pub.token_env_name("미등록채널")
        assert False, "미등록 채널이 하드 실패하지 않음"
    except ValueError:
        pass
    # 신규 채널(config 등록분)도 슬러그로 해석
    assert pub.token_env_name("다람쥐 숏토리") == "YT_REFRESH_TOKEN_DARAMJI"


# ── _credentials: 프로젝트 분리(gcp_project) + 채널별 토큰 조립 (env 조작) ──

def _clear_yt_env():
    for k in list(os.environ):
        if k.startswith(("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")):
            del os.environ[k]


def test_credentials_default_channel_uses_global_client():
    # DEFAULT 프로젝트(재미쇼츠) → 전역 YT_CLIENT_ID/SECRET + 채널 토큰
    _clear_yt_env()
    os.environ.update({"YT_CLIENT_ID": "gid", "YT_CLIENT_SECRET": "gsec",
                       "YT_REFRESH_TOKEN_JAEMISHOTS": "jtok"})
    try:
        c = pub._credentials("재미쇼츠")
        assert c is not None and c.client_id == "gid" and c.refresh_token == "jtok"
    finally:
        _clear_yt_env()


def test_credentials_split_project_uses_scoped_client():
    # P2 프로젝트(킥킥극장) → YT_CLIENT_ID_P2/SECRET_P2 + 채널 토큰. 전역 클라이언트는 안 씀.
    _clear_yt_env()
    os.environ.update({"YT_CLIENT_ID": "gid", "YT_CLIENT_SECRET": "gsec",
                       "YT_CLIENT_ID_P2": "p2id", "YT_CLIENT_SECRET_P2": "p2sec",
                       "YT_REFRESH_TOKEN_KIKKIK": "ktok"})
    try:
        c = pub._credentials("킥킥극장")
        assert c is not None and c.client_id == "p2id" and c.refresh_token == "ktok"
    finally:
        _clear_yt_env()


def test_credentials_missing_token_returns_none():
    _clear_yt_env()
    os.environ.update({"YT_CLIENT_ID": "gid", "YT_CLIENT_SECRET": "gsec"})  # 토큰 없음
    try:
        assert pub._credentials("재미쇼츠") is None
    finally:
        _clear_yt_env()


# ── gate_ok: 발행 *안전* 게이트 (judge quality 는 성과예측 아님) — DB 없이 fake conn ──
class _FakeCur:
    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        pass

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, row):
        self._row = row

    def cursor(self):
        return _FakeCur(self._row)


def test_gate_ok_safe_passes_without_floor():
    # 환각無 + judge 판정 존재 → 안전 통과 (quality 바로 안 막음)
    ok, _ = pub.gate_ok(_FakeConn((0.8, "false")), "cid")
    assert ok is True


def test_gate_ok_low_quality_still_passes_without_floor():
    # 핵심: 낮은 quality 도 floor 미지정이면 통과 — 성과는 사후 벤치마크가 판정
    ok, _ = pub.gate_ok(_FakeConn((0.1, "false")), "cid")
    assert ok is True


def test_gate_ok_blocks_hallucination():
    ok, reason = pub.gate_ok(_FakeConn((0.99, "true")), "cid")
    assert ok is False and "환각" in reason


def test_gate_ok_blocks_when_no_judge():
    # judge 안전판정 자체가 없으면 차단(안전 미확인)
    assert pub.gate_ok(_FakeConn(None), "cid")[0] is False
    assert pub.gate_ok(_FakeConn((None, "false")), "cid")[0] is False


def test_gate_ok_safety_floor_blocks_obviously_broken():
    assert pub.gate_ok(_FakeConn((0.1, "false")), "cid", 0.2)[0] is False
    assert pub.gate_ok(_FakeConn((0.5, "false")), "cid", 0.2)[0] is True


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"{len(fns)} passed")


if __name__ == "__main__":
    _run()
