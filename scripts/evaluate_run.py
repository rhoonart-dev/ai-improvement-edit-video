#!/usr/bin/env python3
"""④ 빠른 루프 오케스트레이터 — ai-video run 1개를 인제스트→피처→judge→게이팅까지 한 번에.

발행 없이(오프라인) 새 쇼츠를 *안전* 평가(환각/깨짐만) → PASS(코호트 발행 가능)/REGENERATE/DISCARD.
judge quality 는 성과예측이 아니라 안전판정용(증거: +14일 성과와 무상관) — 승격은 발행 후
벤치마크 백분위/+14일/paired A-B 가 한다(decide_experiment --metric benchmark).
수동으로 흩어져 있던 단계(T0-1 인제스트 · T1-1 피처 · ① judge)를 하나의 잡으로 묶음.

env: PIPELINE_DB_URL, GEMINI_API_KEY, AI_VIDEO_ROOT
실행:
  ... python scripts/evaluate_run.py --run-dir <outputs/job> --channel "스토리순삭" [--safety-floor 0.2] [--skip-judge]
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

from ingest_aivideo_run import (build_rows, find_channel, find_existing_clip,
                                load_json, upsert_work, write_rows)


def gate(quality, hallucination, safety_floor=None):
    """오프라인 *안전* 게이트. (verdict, reason).

    judge quality 는 성과 예측이 아니다(증거: +14일 성과와 무상관) — '잘 만들어졌으니 발행'을
    여기서 판정하지 않는다. 명백히 환각/깨진 산출물만 거르고, PASS 는 'A/B 코호트로 발행 가능
    (안전)'일 뿐 성과 보증이 아니다. 승격은 발행 후 벤치마크 백분위/+14일/paired A-B 가 한다.
      DISCARD=환각(안전위반) · REGENERATE=safety_floor 미만(명백히 깨짐, opt-in) ·
      REVIEW=judge 실패/생략 · PASS=안전(코호트 발행)."""
    if hallucination:
        return "DISCARD", "환각 가드 위반(안전)"
    if quality is None:
        return "REVIEW", "judge 판정 실패/생략 — 사람 확인"
    if safety_floor is not None and quality < safety_floor:
        return "REGENERATE", f"quality {quality} < 안전바닥 {safety_floor}(명백히 깨짐)"
    return "PASS", f"안전(환각無, quality={quality}) → A/B 코호트 발행(성과는 벤치마크/+14일이 판정)"


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
    ap.add_argument("--safety-floor", type=float, default=None,
                    help="명백히 깨진 산출물 거르는 안전 바닥(quality<floor→REGENERATE). 미지정=judge로 안 거름. "
                         "judge quality는 성과예측 아님 — 승격은 벤치마크/+14일이 판정")
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

        # 4) 안전 게이팅 (성과 아님 — 승격은 사후 벤치마크/+14일)
        verdict, reason = gate(quality, halluc, args.safety_floor)
        print(f"[4/4] ▶ {verdict} — {reason}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
