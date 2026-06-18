#!/usr/bin/env python3
"""⑧ 자동 루프 러너 — 한 번 실행으로 *오프라인 루프 전체*를 자동 처리. cron/스케줄로 주기 실행.

  1) 미처리 run 발견: AI_VIDEO_OUTPUTS/*/{edit_plan,run_log}.json 중 clip_metadata 미존재 → evaluate_run
     (인제스트 → 피처 → judge → 게이팅)
  2) 정합: 미연결 auto_edit ↔ youtube_studio 신규영상 (reconcile, --apply 시 자동 연결)
  3) 상태 리포트

자동화 *밖*(외부 트리거): ai-video 생성 자체, 채널 발행(수동). 이 둘이 일어나면 다음 autoloop 주기가 흡수.
env: PIPELINE_DB_URL, LAEEBLY_DB_URL, GEMINI_API_KEY, AI_VIDEO_ROOT, AI_VIDEO_OUTPUTS(기본 <AI_VIDEO_ROOT>/outputs)
실행(또는 cron):
  PIPELINE_DB_URL=.. LAEEBLY_DB_URL=.. GEMINI_API_KEY=.. AI_VIDEO_ROOT=.. AI_VIDEO_OUTPUTS=.. \
    python scripts/autoloop.py [--quality-min 0.6] [--no-judge] [--apply-reconcile]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

try:
    from envload import load_env
except ImportError:  # 단독 import 컨텍스트
    def load_env(*a, **k):
        return {}

# 작품 → 발행 채널 매핑(정합 대상)
TARGETS = [("로맨스의 절댓값", "스토리순삭"), ("유미의 세포들 시즌3", "재미쇼츠")]


def ingested_run_ids(conn):
    with conn.cursor() as c:
        c.execute("select distinct ai_video_run_id from clip_metadata where ai_video_run_id is not null")
        return {r[0] for r in c.fetchall()}


def discover_new(outputs_dir, done_ids):
    """outputs/*/ 중 edit_plan+run_log 있고 run_log.job_id가 아직 인제스트 안 된 디렉토리."""
    found = []
    for ep in glob.glob(str(Path(outputs_dir) / "*" / "edit_plan.json")):
        d = Path(ep).parent
        rl = d / "run_log.json"
        if not rl.exists():
            continue
        try:
            jid = json.loads(rl.read_text(encoding="utf-8")).get("job_id")
        except (OSError, json.JSONDecodeError):
            jid = None
        if jid and jid not in done_ids:
            found.append((str(d), jid))
    return found


def _channel_for(run_dir):
    """run의 work_title로 발행 채널 추정(없으면 None)."""
    try:
        ep = json.loads((Path(run_dir) / "edit_plan.json").read_text(encoding="utf-8"))
        wt = (ep.get("input") or {}).get("work_title", "")
    except (OSError, json.JSONDecodeError):
        return None
    for work, ch in TARGETS:
        if work and (work in wt or wt in work):
            return ch
    return None


def publish_pass(conn, outputs, quality_min, privacy):
    """PASS(judge≥min·환각無)·미발행 auto_edit 클립을 publish_youtube로 발행 시도(게이트+OAuth 재확인)."""
    import publish_youtube as pub
    from psycopg.rows import dict_row
    with conn.cursor(row_factory=dict_row) as c:
        c.execute("""
            select c.id, m.ai_video_run_id as run_id, ch.name as channel
            from clips c join clip_metadata m on m.clip_id = c.id
            left join channels ch on ch.id = c.channel_id
            where c.source='auto_edit' and c.video_external_id is null
              and exists (select 1 from judge_runs j where j.clip_id = c.id
                          and j.quality_score >= %s
                          and coalesce((j.rubric_scores->>'hallucination_flag')::bool, false) = false)
        """, (quality_min,))
        cands = c.fetchall()
    print(f"[publish] PASS·미발행 후보 {len(cands)}건")
    for r in cands:
        vids = [v for v in glob.glob(str(Path(outputs) / (r["run_id"] or "_none_") / "shorts*.mp4")) if "_480" not in v]
        if not vids:
            print(f"  - clip {str(r['id'])[:8]}: 영상 못찾음(run={r['run_id']})"); continue
        if not r["channel"]:
            print(f"  - clip {str(r['id'])[:8]}: 채널 미상 → 스킵"); continue
        sys.argv = ["pub", "--clip-id", str(r["id"]), "--video", vids[0], "--channel", r["channel"],
                    "--publish", "--privacy", privacy, "--quality-min", str(quality_min)]
        try:
            pub.main()
        except SystemExit:
            pass
        except Exception as e:  # noqa: BLE001
            print(f"  publish 실패: {type(e).__name__} {e}")


def main():
    load_env()  # repo .env 자동 로드(PIPELINE_DB_URL, YT_*, GEMINI_API_KEY 등)
    ap = argparse.ArgumentParser(description="자동 루프 러너")
    ap.add_argument("--quality-min", type=float, default=0.6)
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--apply-reconcile", action="store_true", help="고신뢰·유일 매칭 자동 연결")
    ap.add_argument("--gen", type=int, default=0, help="시작 시 gen_queue pending N건 생성(autogen)")
    ap.add_argument("--auto-publish", action="store_true", help="PASS·미발행 클립 자동 발행(opt-in; 게이트+OAuth 필요)")
    ap.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    a = ap.parse_args()

    outputs = os.environ.get("AI_VIDEO_OUTPUTS") or (os.environ.get("AI_VIDEO_ROOT", "") + "/outputs")
    # 0) 생성 (옵션) — gen_queue pending N건을 ai-video로 자동 생성
    if a.gen:
        import autogen
        print(f"[autoloop] 0) 생성 {a.gen}건 (autogen)")
        sys.argv = ["autogen", "--process", "--limit", str(a.gen)]
        try:
            autogen.main()
        except SystemExit:
            pass
    import psycopg
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    done = ingested_run_ids(conn)
    conn.close()
    new = discover_new(outputs, done)
    print(f"[autoloop] outputs={outputs} | 미처리 run {len(new)}개")

    # 1) 새 run 평가
    import evaluate_run as ev
    for d, jid in new:
        print(f"\n=== evaluate {jid} ===")
        argv = ["ev", "--run-dir", d, "--quality-min", str(a.quality_min)]
        ch = _channel_for(d)
        if ch:
            argv += ["--channel", ch]
        if a.no_judge:
            argv.append("--skip-judge")
        sys.argv = argv
        try:
            ev.main()
        except SystemExit:
            pass
        except Exception as e:  # noqa: BLE001 — 한 run 실패가 루프를 멈추지 않게
            print(f"  evaluate 실패: {type(e).__name__} {e}")

    # 1.5) 자동 발행 (옵션) — PASS·미발행 클립을 publish_youtube로(게이트+OAuth 재확인)
    if a.auto_publish:
        import psycopg as _pg
        _pc = _pg.connect(os.environ["PIPELINE_DB_URL"])
        try:
            publish_pass(_pc, outputs, a.quality_min, a.privacy)
        finally:
            _pc.close()

    # 2) 정합 (reconcile가 미연결 auto_edit 전부 × 2 타깃 채널을 자체 처리)
    import reconcile_published as rc
    print("\n=== reconcile (미연결 auto_edit ↔ youtube_studio) ===")
    argv = ["rc"]
    if a.apply_reconcile:
        argv.append("--apply")
    sys.argv = argv
    try:
        rc.main()
    except SystemExit:
        pass
    except Exception as e:  # noqa: BLE001
        print(f"  reconcile 실패: {type(e).__name__} {e}")

    # 3) 상태 리포트
    print()
    import status_report as sr
    sr.main()


if __name__ == "__main__":
    main()
