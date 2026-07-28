"""find_work_source 순수 함수 단위테스트 — 드라이브 링크 파싱 · 소스 유형 판정 · rclone 명령 (DB 무관).
실행: python scripts/test_find_work_source.py  또는  pytest scripts/test_find_work_source.py
"""
from __future__ import annotations

import find_work_source as fws

# laeebly download_link 실측 형태 — 순수 URL 인 경우와 산문 HTML 에 링크가 박힌 경우가 섞여 있다
PLAIN = "https://drive.google.com/drive/folders/1dp_eAX2WecPk3GXyrt8eMmwtWxNF_Kj-?usp=drive_link"
HTML = '<p>제공분: <a href="https://drive.google.com/drive/folders/130hqZsLmr6_r-phwR0RZxxMOKwTn_HeS">폴더</a></p>'


# ── 드라이브 폴더 ID 파싱 ──

def test_drive_folder_id_from_plain_and_html():
    assert fws.drive_folder_id(PLAIN) == "1dp_eAX2WecPk3GXyrt8eMmwtWxNF_Kj-"
    assert fws.drive_folder_id(HTML) == "130hqZsLmr6_r-phwR0RZxxMOKwTn_HeS"


def test_drive_folder_id_none_when_absent():
    assert fws.drive_folder_id("") is None
    assert fws.drive_folder_id(None) is None
    assert fws.drive_folder_id("https://www.youtube.com/playlist?list=PLx") is None


# ── 소스 유형 판정 ──

def test_classify_gdrive_wins_over_guide():
    # 드라이브 링크가 있으면 guide 에 유튜브 얘기가 있어도 드라이브 제공분이다
    assert fws.classify(PLAIN, "유튜브 채널 업로드분 참고") == "gdrive"


def test_classify_youtube_from_guide():
    assert fws.classify("", "해당 링크 플레이리스트에 있는 영상들만 사용 가능") == "youtube"
    assert fws.classify(None, "tvN joy 또는 놀라운 토요일 유튜브 채널 업로드 클립") == "youtube"


def test_classify_unknown_when_no_signal():
    # 판정 못 하면 추측하지 않는다 — 사람이 guide 를 읽어야 한다
    assert fws.classify("", "") == "unknown"
    assert fws.classify("", "로고는 드라이브에 제공") == "unknown"


# ── rclone 명령 ──

def test_rclone_command_uses_folder_id():
    cmd = fws.rclone_command("1abcDEF", "snl_s8", "/srv/sources")
    assert "--drive-root-folder-id 1abcDEF" in cmd
    assert '"/srv/sources/snl_s8"' in cmd


# ── 슬러그 ──

def test_slugify_keeps_hangul_and_strips_spaces():
    assert fws.slugify("SNL 코리아 리부트 시즌8") == "snl코리아리부트시즌8"
    assert fws.slugify("피의 게임 X") == "피의게임x"


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"{len(fns)} passed")


if __name__ == "__main__":
    _run()
