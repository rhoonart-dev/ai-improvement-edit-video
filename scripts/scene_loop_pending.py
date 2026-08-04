#!/usr/bin/env python3
"""scene_loop 가 확정했지만 **아직 발행되지 않은 장면** 목록을 뽑는다.

scene_loop.py 는 생성만 하고 인제스트·judge·발행은 하지 않는다. 그 뒷단을 사람이나
예약 작업이 처리할 때, "무엇이 남았는지"를 매번 손으로 추론하지 않도록 여기서 판정한다.

판정: results/scene_loop_state.json 의 각 장면(run_id) 에 대해 ves DB(clips) 에
video_external_id 가 있는 클립이 하나라도 있으면 발행됨으로 본다. 없으면 대기.

실행: python scripts/scene_loop_pending.py [--json]
env: PIPELINE_DB_URL (없으면 DB 조회 없이 전체를 '미상'으로 표시)
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
try:
    from envload import load_env
except ImportError:
    def load_env(*a, **k):
        return {}

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE_PATH = REPO_ROOT / "results" / "scene_loop_state.json"


def load_state(path=STATE_PATH):
    if not pathlib.Path(path).exists():
        return {"channels": {}}
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def iter_scenes(state):
    """state → [{channel, work_title, episode, span, run_id, job_dir}] (기록 순서 유지). 순수."""
    out = []
    for ch, cv in (state.get("channels") or {}).items():
        for ep, ev in (cv.get("episodes") or {}).items():
            for s in (ev.get("scenes") or []):
                out.append({
                    "channel": ch,
                    "work_title": cv.get("work_title"),
                    "episode": int(ep),
                    "span": s.get("span"),
                    "run_id": s.get("run_id"),
                    "job_dir": s.get("job_dir"),
                })
    return out


def published_run_ids(conn, run_ids):
    """이미 발행(video_external_id 존재)된 run_id 집합."""
    if not run_ids:
        return set()
    with conn.cursor() as c:
        c.execute("""select distinct m.ai_video_run_id
                     from clip_metadata m join clips cl on cl.id = m.clip_id
                     where m.ai_video_run_id = any(%s) and cl.video_external_id is not null""",
                  (list(run_ids),))
        return {r[0] for r in c.fetchall()}


def shorts_path(job_dir):
    """job 디렉토리의 대표 산출물. scene_loop 는 --max-shorts 1 이라 shorts.mp4 하나다."""
    p = pathlib.Path(job_dir) / "shorts.mp4"
    return str(p) if p.exists() else None


def main():
    load_env()
    ap = argparse.ArgumentParser(description="scene_loop 미발행 장면 목록")
    ap.add_argument("--json", action="store_true", help="JSON 으로 출력")
    a = ap.parse_args()

    scenes = iter_scenes(load_state())
    url = os.environ.get("PIPELINE_DB_URL")
    done = set()
    if url:
        try:
            import psycopg
            with psycopg.connect(url) as conn:
                done = published_run_ids(conn, {s["run_id"] for s in scenes if s["run_id"]})
        except Exception as exc:  # noqa: BLE001 — DB 장애가 목록 출력을 막지 않게
            print(f"[주의] ves DB 조회 실패({type(exc).__name__}) — 발행여부 판정 불가", file=sys.stderr)
    else:
        print("[주의] PIPELINE_DB_URL 미설정 — 발행여부 판정 불가", file=sys.stderr)

    pending = []
    for s in scenes:
        s["published"] = s["run_id"] in done
        s["video"] = shorts_path(s["job_dir"]) if s["job_dir"] else None
        if not s["published"] and s["video"]:
            pending.append(s)

    if a.json:
        print(json.dumps(pending, ensure_ascii=False, indent=2))
        return

    print(f"확정 장면 {len(scenes)}개 · 발행 완료 {sum(1 for s in scenes if s['published'])}개 "
          f"· **미발행 {len(pending)}개**")
    if not pending:
        print("  발행할 것 없음")
        return
    for s in pending:
        sp = s["span"] or []
        print(f"\n  [{s['channel']} · {s['work_title']}] EP{s['episode']}  "
              f"구간 {round(sp[0],1) if sp else '?'}~{round(sp[1],1) if sp else '?'}")
        print(f"    run_id : {s['run_id']}")
        print(f"    video  : {s['video']}")
        print(f"    job_dir: {s['job_dir']}")


if __name__ == "__main__":
    main()
