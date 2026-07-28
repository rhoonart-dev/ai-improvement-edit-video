#!/usr/bin/env python3
"""작품 이름 → 소스를 어디서 받는가. 권리사 DB(laeebly)에서 조회한다.

왜 조회하는가: 소스 위치를 작품 카드나 문서에 손으로 적으면 낡는다. 권리사가 드라이브 폴더를
바꾸면 문서만 틀린 채 남고, 그걸 보고 받은 사람은 옛 회차를 가져온다. laeebly
`licensed_video.download_link` 가 정본이므로 **작품명으로 매번 조회**한다.

소스 유형은 두 갈래다(2026-07-28 실측, 배정 18작품 기준):
  📁 드라이브 제공분  download_link 에 구글 드라이브 폴더 링크 — rclone 으로 받는다
  ▶️ 유튜브          download_link 가 비어 있고 guide 가 유튜브 채널·플레이리스트를 지정 —
                    ai-video 가 --youtube-url 로 직접 받으므로 사람이 받을 게 없다

실행:
  python scripts/find_work_source.py                    # 배정된 작품 전부
  python scripts/find_work_source.py "SNL 코리아 리부트 시즌8"
  python scripts/find_work_source.py --rclone "원희는 스무살"   # 받는 명령까지 출력
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import channel_registry as reg  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
FOLDER_RE = re.compile(r"/folders/([A-Za-z0-9_-]{20,})")
URL_RE = re.compile(r"https?://[^\s\"'<>]+")


# ─────────────────────────── 순수 파싱 ───────────────────────────

def drive_folder_id(download_link):
    """download_link → 구글 드라이브 폴더 ID(없으면 None). 순수.

    laeebly 의 값은 산문 HTML 일 수도 있어 링크만 뽑는다."""
    m = FOLDER_RE.search(str(download_link or ""))
    return m.group(1) if m else None


def classify(download_link, guide=""):
    """소스 유형 판정 → 'gdrive' | 'youtube' | 'unknown'. 순수."""
    if drive_folder_id(download_link):
        return "gdrive"
    if re.search(r"youtube|유튜브|플레이리스트", str(guide or ""), re.I):
        return "youtube"
    return "unknown"


def rclone_command(folder_id, dest_slug, sources_root):
    """드라이브 폴더 ID → 받는 명령. 순수.

    ⚠️ --drive-root-folder-id 를 쓴다. 공유받은 폴더는 --drive-shared-with-me 로도 보이지만
    목록에 안 뜨는 경우가 있어(실측) 폴더 ID 직접 지정이 확실하다."""
    dest = pathlib.Path(sources_root) / dest_slug
    return (f'rclone copy "gdrive:" "{dest}" '
            f'--drive-root-folder-id {folder_id} -P')


def slugify(work):
    """작품명 → 소스 폴더 슬러그 후보(사람이 바꿔도 된다). 순수."""
    s = re.sub(r"[^\w가-힣]+", "_", reg.norm_work_title(work))
    return s.strip("_").lower()[:32]


# ─────────────────────────── 조회 ───────────────────────────

def fetch_rows(titles=None):
    import psycopg
    url = os.environ.get("LAEEBLY_DB_URL")
    if not url:
        sys.exit("LAEEBLY_DB_URL 미설정 — .env 를 로드했는지 확인하세요")
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("select title, download_link, guide, identification_code from licensed_video")
        rows = {t: (dl, g, code) for t, dl, g, code in cur.fetchall()}
    return rows


def assigned_works():
    """배정된 채널들의 작품 목록(중복 제거)."""
    recs = reg.load_channels()
    return sorted({w for r in recs for w in (r.get("works") or [])})


def main():
    ap = argparse.ArgumentParser(description="작품 이름 → 소스 출처(권리사 DB 조회)")
    ap.add_argument("work", nargs="*", help="작품명(생략하면 배정된 작품 전부)")
    ap.add_argument("--rclone", action="store_true", help="드라이브 소스는 받는 명령까지 출력")
    a = ap.parse_args()

    rows = fetch_rows()
    works = a.work or assigned_works()
    local = reg.load_machine_local()
    sources_root = local.get("sources_root") or reg.default_sources_root()

    for w in works:
        key = w if w in rows else (w + " (g)" if (w + " (g)") in rows else None)
        print(f"■ {w}")
        if key is None:
            cand = [t for t in rows if reg.same_work_title(t, w)]
            print(f"    ⛔ laeebly 에 없습니다"
                  + (f" — 후보: {cand}" if cand else " (외부 계약이면 작품 카드에 "
                                                    "rights_lookup:'none' 과 근거를 적으세요)"))
            print()
            continue
        dl, guide, code = rows[key]
        kind = classify(dl, guide)
        fid = drive_folder_id(dl)
        if kind == "gdrive":
            print(f"    📁 구글 드라이브 · 폴더 {fid}")
            print(f"       https://drive.google.com/drive/folders/{fid}")
            if a.rclone:
                print(f"       {rclone_command(fid, slugify(w), sources_root)}")
        elif kind == "youtube":
            print(f"    ▶️ 유튜브 — 사람이 받을 것 없음(ai-video 가 --youtube-url 로 직접 받는다)")
            print(f"       범위는 config/works.json 의 source.type·url 이 정본. 근거는 guide:")
            g = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(guide or ""))).strip()
            print(f"       {g[:150]}")
        else:
            urls = URL_RE.findall(str(dl or ""))
            print(f"    ⚠️ 소스 출처를 판정하지 못했습니다"
                  + (f" — download_link: {urls[0]}" if urls else " (download_link 비어 있음)"))
        print(f"       식별코드 {code}")
        print()


if __name__ == "__main__":
    main()
