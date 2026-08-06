#!/usr/bin/env python3
"""재개(--from-step)로 만든 job 의 테이크들을 진행 상태에 심는다.

왜 필요한가: 루프는 **자기가 띄운 생성**만 상태에 기록한다. 사람이 손으로 재개한 job 은
산출물이 있어도 상태에 없어서 검수함에 올라가지 않고, 다음 실행이 같은 대목을 다시 만든다
(런북 §6-2 의 '과거 산출물 심기'와 같은 문제). 이 스크립트가 그 자리를 메운다.

루프와 같은 규칙을 쓴다 — 기존 장면·앞 테이크와 겹치는 테이크는 심지 않는다.

실행:
  python scripts/seed_resumed_takes.py --job-dir <job> --channel <채널> --work <작품> \
      --episode <N> --source <소스경로> [--dry-run]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import scene_loop as sl  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="재개 job 의 테이크를 상태에 심는다")
    ap.add_argument("--job-dir", required=True)
    ap.add_argument("--channel", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--episode", type=int, required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    cfg = sl.registry.load_loop_policy()
    iou_th = cfg.get("dup_iou_threshold", 0.5)
    ctol = cfg.get("dup_center_tolerance_sec", 15)

    state = sl.load_state()
    ep = ((state.get("channels") or {}).get(a.channel) or {}).get("episodes", {}).get(str(a.episode), {})
    prior = [s["span"] for s in (ep.get("scenes") or []) if s.get("span")]

    run_id = sl._run_id_of(a.job_dir)
    added = []
    for label, plan_path, _video in sl.job_takes(a.job_dir):
        span = sl.scene_span(sl.json.loads(plan_path.read_text(encoding="utf-8")))
        if span is None:
            print(f"  ⚠ {label}: 구간 없음 → 건너뜀")
            continue
        if sl.is_duplicate(span, prior, iou_th, ctol):
            print(f"  ↻ {label}: 기존/앞 테이크와 중복 {span} → 심지 않음")
            continue
        prior.append(span)
        added.append((sl.take_label(label), span))
        if not a.dry_run:
            sl.record_scene(state, a.channel, a.work, a.episode, a.source, span, run_id,
                            str(a.job_dir), channel=a.channel, take=sl.take_label(label))
    if added and not a.dry_run:
        sl.save_state(state)
    print(f"[seed] {a.channel} EP{a.episode} run={run_id} — "
          + (" · ".join(f"{t} {s}" for t, s in added) if added else "심을 것 없음")
          + (" (dry-run)" if a.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
