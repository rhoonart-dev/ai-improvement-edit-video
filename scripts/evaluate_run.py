#!/usr/bin/env python3
"""④ 빠른 루프 오케스트레이터 — ai-video run 1개를 인제스트→피처→judge→게이팅까지 한 번에.

발행 없이(오프라인) 새 쇼츠를 평가해 PASS(발행 후보) / REGENERATE / DISCARD 판정.
수동으로 흩어져 있던 단계(T0-1 인제스트 · T1-1 피처 · ① judge)를 하나의 잡으로 묶음.

env: PIPELINE_DB_URL, GEMINI_API_KEY, AI_VIDEO_ROOT
실행:
  ... python scripts/evaluate_run.py --run-dir <outputs/job> --channel "스토리순삭" [--quality-min 0.6] [--skip-judge]
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

from ingest_aivideo_run import (build_rows, find_channel, find_existing_clip,
                                load_json, upsert_work, write_rows)


def gate(quality, hallucination, quality_min=0.6):
    """오프라인 게이트 판정. (verdict, reason)."""
    if hallucination:
        return "DISCARD", "환각 가드 위반"
    if quality is None:
        return "REVIEW", "judge 판정 실패/생략"
    if quality >= quality_min:
        return "PASS", f"quality {quality} ≥ {quality_min} (발행 후보)"
    return "REGENERATE", f"quality {quality} < {quality_min}"


def _find_short(run_dir):
    for pat in ("shorts.mp4", "shorts_*.mp4", "*.mp4"):
        hits = sorted(h for h in glob.glob(str(Path(run_dir) / pat))
                      if "_480" not in h and "rough" not in h)
        if hits:
            return hits[0]
    return None


def main():
    ap = argparse.ArgumentParser(description="빠른 루프 오케스트레이터 (인제스트→피처→judge→게이팅)")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--channel", default=None)
    ap.add_argument("--short-label", default=None)
    ap.add_argument("--quality-min", type=float, default=0.6)
    ap.add_argument("--ai-video-root", default=os.environ.get("AI_VIDEO_ROOT", "/Users/gimsewon/rhoonart/ai-video"))
    ap.add_argument("--skip-judge", action="store_true")
    args = ap.parse_args()

    run_dir = Path(args.run_dir)
    edit_plan = load_json(run_dir / "edit_plan.json")
    run_log = load_json(run_dir / "run_log.json")
    if edit_plan is None:
        sys.exit(f"edit_plan.json 없음: {run_dir}")
    short = _find_short(run_dir)
    if short is None:
        sys.exit(f"shorts.mp4 없음: {run_dir}")

    import psycopg
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    try:
        # 1) 인제스트 (provenance)
        clip, meta, _ = build_rows(edit_plan, run_log, run_dir, short_label=args.short_label,
                                   content_id=None, is_exploration=True, ai_video_root=args.ai_video_root)
        work_id = upsert_work(conn, clip["work_title"])
        channel_id = find_channel(conn, args.channel)
        existing = find_existing_clip(conn, meta["ai_video_run_id"], clip["episode"])
        clip_id = write_rows(conn, clip, meta, work_id=work_id, channel_id=channel_id, existing_clip_id=existing)
        print(f"[1/4] 인제스트 clip {clip_id} (run={meta['ai_video_run_id']}, dur={clip['duration_sec']})")

        # 2) 피처 추출 (det)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "extract"))
        from feature_extractor import extract_features, upsert_features
        f = extract_features(short, meta={"title": clip["work_title"]},
                             with_semantic=False, with_ocr=False, with_obs=False)
        upsert_features(conn, clip_id, "det-v1", f)
        print(f"[2/4] 피처 det-v1 (silence={f.get('silence_ratio')}, cut={f.get('cut_count')})")

        # 3) judge
        quality, halluc = None, False
        if not args.skip_judge:
            import run_judge as rj
            j = rj.judge_video(short, os.environ["GEMINI_API_KEY"])
            rj.write_judge(conn, clip_id, j)
            quality = j["quality_score"]
            halluc = j["rubric_scores"]["hallucination_flag"]
            print(f"[3/4] judge quality={quality} dims={ {k: j['rubric_scores'][k] for k in rj.DIMS} } halluc={halluc}")
        else:
            print("[3/4] judge 생략")

        # 4) 게이팅
        verdict, reason = gate(quality, halluc, args.quality_min)
        print(f"[4/4] ▶ {verdict} — {reason}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
