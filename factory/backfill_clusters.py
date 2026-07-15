#!/usr/bin/env python
"""cluster_id 없는 쇼츠(주로 비라이선스) 백필 — 저장된 description + 로컬 영상으로
   원작 식별 → eb_ip 등록 → cluster_id/is_laeebly_licensed 갱신. 재다운로드·재VLM 없음.
사용: python backfill_clusters.py [--limit N]"""
import argparse
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from cluster import IPRegistry
from config import DOWNLOADS, load_settings
from db import Pipeline


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    cfg = load_settings()
    pipe = Pipeline(cfg["PIPELINE_URL"], cfg["PIPELINE_SERVICE_KEY"])
    reg = IPRegistry(pipe, cfg.get("GEMINI_API_KEY", ""), cfg.get("GEMINI_MODEL"))

    rows = pipe.select("eb_shorts_features", {
        "select": ("shorts_id,title_text,licensed_video_title,identification_code,"
                   "description_text,lifecycle_status"),
        "cluster_id": "is.null", "lifecycle_status": "eq.active"})
    if args.limit:
        rows = rows[:args.limit]
    print(f"[backfill] cluster 없는 active 쇼츠 {len(rows)}개\n")
    done = 0
    for r in rows:
        sid = r["shorts_id"]
        vid = next((DOWNLOADS / f"{sid}{e}" for e in (".mp4", ".webm", ".mkv")
                    if (DOWNLOADS / f"{sid}{e}").exists()), None)
        ctl = {"identification_code": r.get("identification_code"),
               "title_text": r.get("title_text"),
               "licensed_video_title": r.get("licensed_video_title")}
        try:
            cid, tone, lic, has_src, ip_key = reg.resolve(
                ctl, description=r.get("description_text"), video_path=vid)
        except Exception as e:
            print(f"  {sid}: 실패 {str(e)[:80]}")
            continue
        pipe.upsert("eb_shorts_features",
                    [{"shorts_id": sid, "cluster_id": cid, "is_laeebly_licensed": lic,
                      "has_source_video": has_src, "ip_key": ip_key}],
                    on_conflict="shorts_id")
        done += 1
        tag = "자체제작" if has_src is False else f"cluster={cid}"
        print(f"  {sid:14} → {tag}  (영상 {'有' if vid else '無'})")
    print(f"\n[backfill] {done}개 갱신 | 신규 비라이선스 원작 {reg.n_unlicensed}개 eb_ip 등록")


if __name__ == "__main__":
    main()
