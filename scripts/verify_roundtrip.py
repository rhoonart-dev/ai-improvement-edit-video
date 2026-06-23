#!/usr/bin/env python3
"""Phase 0 라운드트립 검증 — 배포된 파이프라인 DB(PIPELINE_DB_URL)에 T0-1 인제스트가
실제로 동작하는지 실측. 트랜잭션 insert → 역추적 SELECT → ROLLBACK(기본, 무오염).

기획서 T0-1 완료기준("임의 쇼츠 1개 → run/edit_plan/config 역추적")의 라이브 실증.

실행:
  PIPELINE_DB_URL=... python scripts/verify_roundtrip.py          # 롤백(무오염)
  PIPELINE_DB_URL=... python scripts/verify_roundtrip.py --keep   # 커밋(실데이터로 남김)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ingest_aivideo_run import build_rows, upsert_work, write_rows

_SYNTH_EDIT_PLAN = {
    "input": {"video_path": "/__verify__/long.mp4", "work_title": "__verify_roundtrip__",
              "topic": "라운드트립 검증", "language": "ko"},
    "layout": {"canvas": "1080x1920", "top_title": "검증\n라운드트립", "bottom_label": "__verify_roundtrip__"},
    "timeline": [
        {"role": "hook", "clip_start_sec": 3.0, "clip_end_sec": 11.0},
        {"role": "payoff", "clip_start_sec": 60.0, "clip_end_sec": 102.0},
    ],
    "audio_mix": {"tts_gain_db": -3, "original_gain_db": -3, "bgm_gain_db": -20},
}
_SYNTH_RUN_LOG = {"job_id": "__verify_roundtrip__run", "input": _SYNTH_EDIT_PLAN["input"], "steps": []}


def main():
    ap = argparse.ArgumentParser(description="Phase 0 라운드트립 검증")
    ap.add_argument("--keep", action="store_true", help="롤백 대신 커밋(실데이터로 남김)")
    args = ap.parse_args()

    import psycopg
    url = os.environ.get("PIPELINE_DB_URL")
    if not url:
        sys.exit("PIPELINE_DB_URL 미설정")

    clip, meta, _ = build_rows(
        _SYNTH_EDIT_PLAN, _SYNTH_RUN_LOG, Path("/nonexistent"),
        short_label="roundtrip", content_id=None, is_exploration=False, ai_video_root="/nonexistent",
    )
    conn = psycopg.connect(url)
    conn.autocommit = False
    try:
        work_id = upsert_work(conn, clip["work_title"])
        clip_id = write_rows(conn, clip, meta, work_id=work_id, channel_id=None,
                             existing_clip_id=None, commit=False)
        with conn.cursor() as cur:
            cur.execute(
                """select c.source, c.duration_sec, w.title, m.ai_video_run_id, m.ingest_source,
                          m.edit_plan->'input'->>'topic'              as topic,
                          jsonb_array_length(m.edit_plan->'timeline')  as timeline_len
                   from clips c
                   join clip_metadata m on m.clip_id = c.id
                   left join works w on w.id = c.work_id
                   where c.id = %s""",
                (clip_id,),
            )
            row = dict(zip([d.name for d in cur.description], cur.fetchone()))
        print("INSERT OK  clip_id:", clip_id)
        print("역추적 (clip_id → run / edit_plan / work):")
        for k, v in row.items():
            print(f"   {k}: {v}")
        assert row["source"] == "auto_edit"
        assert row["ai_video_run_id"] == "__verify_roundtrip__run"
        assert row["timeline_len"] == 2
        if args.keep:
            conn.commit()
            print(f"\nPERSISTED (--keep). 정리: delete from clips where id='{clip_id}';")
        else:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute("select count(*) from clips where source='auto_edit'")
                print(f"\nROLLED BACK — 무오염. 현재 auto_edit clips: {cur.fetchone()[0]}")
        print("\n✅ 라운드트립 검증 통과 (T0-1 완료기준 충족)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
