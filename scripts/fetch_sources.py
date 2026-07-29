#!/usr/bin/env python3
"""회차 소스를 작품 폴더에 미리 받아둔다 — 밤 실행이 다운로드로 시간을 쓰지 않게.

루프도 캐시가 없으면 스스로 받지만(source_cache), 미리 받아두면 생성만 하면 되므로 야간 실행이
짧아지고 네트워크 실패로 그날 채널이 통째로 빠지는 일이 줄어든다.

실행:
  python scripts/fetch_sources.py                          # 이 머신 담당 전 작품
  python scripts/fetch_sources.py "놀라운 토요일"
  python scripts/fetch_sources.py "놀라운 토요일" --episodes 426-428
  python scripts/fetch_sources.py --dry-run                # 무엇을 받을지만

📁 드라이브 제공분 작품은 여기서 받지 않는다 — rclone 명령은
   `python scripts/find_work_source.py "<작품>" --rclone` 이 알려준다.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import channel_registry as registry  # noqa: E402
import scene_loop as sl  # noqa: E402
import source_cache as sc  # noqa: E402


def parse_episodes(spec):
    """'426-428' | '1,3,5' | '7' → 정수 집합(없으면 None=전부). 순수."""
    if not spec:
        return None
    out = set()
    for part in str(spec).split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out or None


def main():
    ap = argparse.ArgumentParser(description="회차 소스를 작품 폴더에 미리 받아둔다")
    ap.add_argument("work", nargs="*", help="작품명(생략하면 이 머신 담당 전부)")
    ap.add_argument("--episodes", help="회차 범위 — '426-428' 또는 '1,3,5'")
    ap.add_argument("--limit", type=int, default=3,
                    help="작품당 최대 몇 회차까지 받을지(기본 3 — 회차당 100MB 안팎이라 무제한은 위험)")
    ap.add_argument("--dry-run", action="store_true", help="받지 않고 계획만")
    a = ap.parse_args()

    sl.load_env()
    want = parse_episodes(a.episodes)
    local = registry.load_machine_local()
    sources_root = local.get("sources_root") or registry.default_sources_root()
    ai_video_root = os.environ.get("AI_VIDEO_ROOT") or str(pathlib.Path.home() / "ves" / "ai-video")
    gen_py = os.environ.get("AI_VIDEO_GEN_PY",
                            str(pathlib.Path(ai_video_root) / ".venv" / "bin" / "python"))

    def log(m):
        print(m, flush=True)

    chans = registry.effective_channel_configs()
    if a.work:
        chans = [c for c in chans if any(registry.same_work_title(c["work_title"], w) for w in a.work)]
        if not chans:
            sys.exit(f"작품 {a.work} 이(가) 이 머신 담당에 없습니다")

    total_new = 0
    for ch in chans:
        work = ch["work_title"]
        log(f"\n■ {work} (채널 {ch['channel']})")
        if ch.get("_source_kind") == "local":
            log("   📁 드라이브 제공분 — find_work_source.py --rclone 로 받으세요")
            continue
        try:
            eps = sl.discover_episodes_for(ch, gen_py, 24, log)
        except Exception as e:  # noqa: BLE001
            log(f"   ✗ 회차 목록 실패: {type(e).__name__}: {e}")
            continue

        picked = [(n, u) for n, u in eps if want is None or n in want]
        if want is None:
            picked = picked[:a.limit]
        if not picked:
            log("   받을 회차 없음")
            continue

        for ep_num, url in picked:
            slug = sc.work_slug(work, ch.get("dir_slug"))
            d = sc.episode_dir(sources_root, slug, ep_num)
            state, why = sc.cache_state(d, sc.youtube_video_id(url))
            if state == "hit":
                log(f"   EP{ep_num}: 이미 있음 — {d}")
                continue
            if state == "mismatch":
                log(f"   ⛔ EP{ep_num}: {why}")
                continue
            if a.dry_run:
                log(f"   (dry-run) EP{ep_num} 받을 예정 → {d}")
                continue
            try:
                sc.ensure_episode_source(ch, ep_num, url, gen_py=gen_py,
                                         ai_video_root=ai_video_root,
                                         sources_root=sources_root, log=log)
                total_new += 1
            except Exception as e:  # noqa: BLE001
                log(f"   ✗ EP{ep_num} 실패: {type(e).__name__}: {e}")

    log(f"\n새로 받은 회차: {total_new}개 · 캐시 위치 {sources_root}")
    log("⚠️ 소스는 자동으로 지워지지 않는다 — 정리는 SCENE_LOOP_OPERATIONS.md §6-3")


if __name__ == "__main__":
    main()
