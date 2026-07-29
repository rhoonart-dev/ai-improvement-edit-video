#!/usr/bin/env python3
"""회차 소스 캐시 — 작품 폴더에 한 번만 받아 두고 재사용한다.

왜 필요한가(2026-07-28 실측): 루프는 1회 실행에 채널당 1장면만 만든다. 회차당 3장면을 채우려면
3번 실행해야 하는데, 매 실행이 `create_shorts --youtube-url` 을 새 outdir 로 불러 **같은 영상을
매번 새로 받았다.** 흥행수집 EP1 은 83MB 짜리가 3벌, 너굴안방 EP1 은 171MB 짜리가 2벌 쌓여
있었다(md5 동일). 회차당 소스 100MB 기준 채널마다 200MB 씩 중복이다.

캐시 배치 — 작품마다 폴더, 그 안에 회차마다 폴더:
    <sources_root>/<작품슬러그>/ep<NNN>/
        source.mp4
        source.ko.srt      (자막이 있으면)
        meta.json          어느 영상을 받았는지(video_id·제목·길이·받은 시각)

★ 회차 폴더 구조를 ai-video 다운로더의 out_dir 계약(source.mp4 + source.<lang>.srt)과 **같게**
맞췄다. 그래서 다운로드를 그 모듈에 그대로 위임할 수 있고, 캐시가 파이프라인이 직접 받는 것과
동일한 결과를 낸다(자막 우선순위·포맷 선택까지).

🛑 meta.json 의 video_id 대조가 이 캐시의 안전장치다. 로컬 파일을 소스로 쓰면 '이 파일이 정말
그 회차인가' 를 확인할 방법이 사라진다 — 실제로 사람이 받아둔 파일이 다른 시즌 영상이었는데
아무도 모른 채 발행된 사고가 있었다(2026-07-26 '여배우 은진'). 루프가 인덱스에서 고른 영상 ID 와
캐시의 video_id 가 다르면 생성하지 않고 멈춘다.
"""
from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from datetime import datetime
from pathlib import Path

VIDEO_NAME = "source.mp4"
META_NAME = "meta.json"
SUB_GLOBS = ("source.ko.srt", "source.ko-*.srt", "source.*.srt")


# ─────────────────────────── 순수 (단위테스트) ───────────────────────────

def work_slug(work_title, explicit=None):
    """작품명 → 소스 폴더 이름. 카드에 dir_slug 가 있으면 그걸 쓴다. 순수.

    NFC 로 접는 이유: macOS 가 한글을 NFD 로 주면 눈으로 같은 폴더가 둘 생긴다."""
    if explicit:
        return explicit
    s = unicodedata.normalize("NFC", work_title or "")
    s = re.sub(r"[^\w가-힣]+", "_", s).strip("_").lower()
    return s[:48] or "unknown"


def episode_dir(sources_root, slug, ep_num):
    """<sources_root>/<슬러그>/ep<NNN>. 순수."""
    return Path(sources_root) / slug / f"ep{int(ep_num):03d}"


def youtube_video_id(url):
    """watch URL → 영상 ID(없으면 None). 순수."""
    m = re.search(r"[?&]v=([A-Za-z0-9_-]{6,})", str(url or ""))
    return m.group(1) if m else None


def find_subtitle(ep_dir):
    """회차 폴더에서 자막 파일(없으면 None). 순수(파일시스템 조회만)."""
    d = Path(ep_dir)
    for pat in SUB_GLOBS:
        hits = sorted(d.glob(pat))
        if hits:
            return hits[0]
    return None


def read_meta(ep_dir):
    p = Path(ep_dir) / META_NAME
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_meta(ep_dir, **fields):
    p = Path(ep_dir) / META_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(fields, ensure_ascii=False, indent=2), encoding="utf-8")


def cache_state(ep_dir, expect_video_id=None):
    """캐시 상태 판정 → ('hit'|'miss'|'mismatch', 사유). 순수 판정.

    mismatch 는 **회차 오배정**이다. 같은 회차 폴더에 다른 영상이 들어 있다는 뜻이라, 지우고
    다시 받기 전에는 생성하면 안 된다."""
    d = Path(ep_dir)
    if not (d / VIDEO_NAME).exists():
        return "miss", "캐시 없음"
    if expect_video_id:
        got = read_meta(d).get("video_id")
        if got and got != expect_video_id:
            return "mismatch", (f"캐시에 다른 영상이 들어 있습니다 — 있음 {got} / 필요 "
                                f"{expect_video_id}. 그 회차 폴더를 지우고 다시 받으세요: {d}")
        if not got:
            return "hit", "meta.json 없음(옛 캐시) — video_id 대조 없이 사용"
    return "hit", "캐시 사용"


# ─────────────────────────── 다운로드 (ai-video 위임) ───────────────────────────

_DL_SNIPPET = """
import json, sys
from pathlib import Path
from app.modules.youtube_downloader import download_youtube_assets
a = download_youtube_assets(sys.argv[1], Path(sys.argv[2]), lang="ko")
print(json.dumps({"video": str(a.video_path),
                  "subtitle": str(a.subtitle_path) if a.subtitle_path else None}))
"""


def download_episode(gen_py, ai_video_root, url, ep_dir, timeout=1800):
    """유튜브 회차 영상을 캐시 폴더에 받는다 → (video_path, subtitle_path|None).

    ★ ai-video 의 download_youtube_assets 에 그대로 위임한다. 포맷 선택·자막 우선순위를 여기서
    다시 구현하면 캐시로 만든 소스가 파이프라인이 직접 받은 것과 미묘하게 달라진다."""
    Path(ep_dir).mkdir(parents=True, exist_ok=True)
    r = subprocess.run([gen_py, "-c", _DL_SNIPPET, url, str(ep_dir)],
                       cwd=str(ai_video_root), capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"소스 다운로드 실패 rc={r.returncode}: {(r.stderr or '')[-300:]}")
    line = [l for l in (r.stdout or "").strip().splitlines() if l.startswith("{")]
    if not line:
        raise RuntimeError(f"다운로드 결과를 읽지 못했습니다: {(r.stdout or '')[-200:]}")
    out = json.loads(line[-1])
    return Path(out["video"]), (Path(out["subtitle"]) if out.get("subtitle") else None)


def ensure_episode_source(ch, ep_num, source, *, gen_py, ai_video_root, sources_root,
                          log=lambda m: None, allow_download=True):
    """회차 소스를 캐시에 두고 (영상경로, 자막경로|None) 을 돌려준다.

    로컬 소스 작품은 이미 파일이므로 그대로 통과시킨다(자막은 ai-video 가 알아서 찾는다).
    유튜브 소스는 캐시를 보고, 없으면 받아 채운다.

    🛑 캐시에 다른 영상이 들어 있으면(mismatch) 생성하지 않고 예외를 낸다."""
    if not str(source or "").startswith("http"):
        return Path(source), None

    slug = work_slug(ch.get("work_title"), ch.get("dir_slug"))
    d = episode_dir(sources_root, slug, ep_num)
    vid = youtube_video_id(source)

    state, why = cache_state(d, vid)
    if state == "mismatch":
        raise ValueError(f"{ch.get('work_title')} EP{ep_num}: {why}")
    if state == "hit":
        log(f"  [소스] 캐시 사용 EP{ep_num} — {d}")
        return d / VIDEO_NAME, find_subtitle(d)

    if not allow_download:
        raise FileNotFoundError(f"{ch.get('work_title')} EP{ep_num}: 캐시가 없습니다 {d} "
                                f"— scripts/fetch_sources.py 로 미리 받으세요")

    log(f"  [소스] EP{ep_num} 캐시에 받는 중 (한 번만 받고 이후 재사용) — {d}")
    video, sub = download_episode(gen_py, ai_video_root, source, d)
    write_meta(d, work=ch.get("work_title"), episode=int(ep_num), video_id=vid,
               source_url=source, subtitle=(sub.name if sub else None),
               fetched_at=datetime.now().isoformat(timespec="seconds"))
    log(f"  [소스] 저장 완료 ({video.stat().st_size / 1048576:.0f}MB"
        + (f" · 자막 {sub.name}" if sub else " · 자막 없음") + ")")
    return video, sub
