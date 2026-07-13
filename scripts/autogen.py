#!/usr/bin/env python3
"""생성 자동화 — gen_queue의 pending 잡을 ai-video(cut-4+T0-2 worktree)로 자동 생성.

사용자는 소스(work+영상)를 큐에 넣고(--enqueue), autogen이 실행(--process). 산출 run 은
autoloop 이 인제스트·평가한다. 즉 "생성 명령을 사람이 매번 치는 것"을 자동화한다.
(무엇을 만들지 = 소스 선택은 사람/전략 입력. 콘텐츠 자율수집은 별도 단계.)

env: PIPELINE_DB_URL, GEMINI_API_KEY,
     AI_VIDEO_WORKTREE(생성 실행 디렉토리, 기본 ../ai-video-t0-2), AI_VIDEO_GEN_PY(기본 ai-video .venv python),
     AI_VIDEO_ROOT
실행:
  enqueue: ... autogen.py --enqueue --work "로맨스의 절댓값" --source /path/EP06.mp4 --channel 스토리순삭 [--topic .. --episode 6 --max-shorts 1]
  process: ... autogen.py --process [--limit 1] [--dry-run]
  list:    ... autogen.py --list
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

DEFAULT_WT = "/Users/gimsewon/rhoonart/ai-video-t0-2"
DEFAULT_PY = "/Users/gimsewon/rhoonart/ai-video/.venv/bin/python"


# ─────────────────────────── 순수 (단위테스트) ───────────────────────────

def build_gen_cmd(job, python, worktree, no_research=True, flags=None):
    """gen_queue job(dict) → ai-video create_shorts argv. (worktree는 cwd/PYTHONPATH로 별도 전달)

    §3-4 생성 경로 설정 통일: 노브 플래그(flags)를 generate_batch 경로와 동일하게 부착.
      flags=None(기본) → generate_batch.GOOD_FLAGS (현행 챔피언 config)
      flags=[...]      → 라운드 config (loop_controller.config_to_flags 산출물)
      flags=[]         → 플래그 없이 (노브 미지원 구 worktree 호환 — A/B 코호트엔 쓰지 말 것)
    ⚠ AI_VIDEO_WORKTREE 는 cut-4 노브(--silence-profile 등)를 지원하는 브랜치여야 한다.
      미지원 브랜치면 argparse 에러로 '요란하게' 실패한다 — 조용히 다른 설정으로 생성되는 것보다 안전."""
    if flags is None:
        from generate_batch import GOOD_FLAGS
        flags = GOOD_FLAGS
    src = str(job["source"])
    cmd = [python, "-m", "app.cli", "create_shorts",
           "--title", job["work_title"],
           "--max-shorts", str(job.get("max_shorts") or 1)]
    cmd += (["--youtube-url", src] if src.startswith("http") else ["--video", src])
    if job.get("topic"):
        cmd += ["--topic", job["topic"]]
    if job.get("episode") is not None:
        cmd += ["--episode", str(job["episode"])]
    if no_research:
        cmd += ["--no-research"]
    cmd += list(flags)
    return cmd


def parse_job_id(stdout):
    """ai-video stdout에서 run_log 경로 → job_id(디렉토리명). 못 찾으면 None."""
    m = re.search(r"outputs/([^/\s]+)/run_log\.json", stdout or "")
    return m.group(1) if m else None


# ─────────────────────────── DB / 실행 ───────────────────────────

def enqueue(conn, work, source, channel, topic, episode, max_shorts):
    with conn.cursor() as c:
        c.execute("""insert into public.gen_queue(work_title, source, channel, topic, episode, max_shorts)
                     values (%s, %s, %s, %s, %s, %s) returning id""",
                  (work, source, channel, topic, episode, max_shorts))
        rid = c.fetchone()[0]
    conn.commit()
    return rid


def fetch_pending(conn, limit):
    from psycopg.rows import dict_row
    with conn.cursor(row_factory=dict_row) as c:
        c.execute("select * from public.gen_queue where status='pending' order by created_at limit %s", (limit,))
        return c.fetchall()


def set_status(conn, jid, status, run_id=None, error=None):
    with conn.cursor() as c:
        c.execute("""update public.gen_queue set status=%s, run_id=coalesce(%s, run_id),
                     error=%s, updated_at=now() where id=%s""", (status, run_id, error, jid))
    conn.commit()


def main():
    ap = argparse.ArgumentParser(description="생성 자동화 (gen_queue → ai-video)")
    ap.add_argument("--enqueue", action="store_true")
    ap.add_argument("--process", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--work"); ap.add_argument("--source"); ap.add_argument("--channel")
    ap.add_argument("--topic"); ap.add_argument("--episode", type=int)
    ap.add_argument("--max-shorts", type=int, default=1)
    ap.add_argument("--limit", type=int, default=1)
    ap.add_argument("--research", action="store_true", help="작품 리서치 포함(기본 생략=빠름)")
    # §3-4: 라운드 config 주입(generate_batch 와 동일 인터페이스). 미지정=GOOD_FLAGS(챔피언).
    ap.add_argument("--silence-profile", default=None)
    ap.add_argument("--length-profile", default=None)
    ap.add_argument("--loudness-lufs", default=None)
    ap.add_argument("--no-knob-flags", action="store_true",
                    help="노브 플래그 미부착(노브 미지원 구 worktree 호환 — A/B 코호트 금지)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    flags = None                                   # None → GOOD_FLAGS
    if a.no_knob_flags:
        flags = []
    elif a.silence_profile or a.length_profile or a.loudness_lufs:
        from generate_batch import GOOD_FLAGS
        d = dict(zip(GOOD_FLAGS[::2], GOOD_FLAGS[1::2]))
        if a.silence_profile:
            d["--silence-profile"] = a.silence_profile
        if a.length_profile:
            d["--length-profile"] = a.length_profile
        if a.loudness_lufs:
            d["--loudness-lufs"] = a.loudness_lufs
        flags = [x for kv in d.items() for x in kv]

    import psycopg
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    try:
        if a.enqueue:
            if not (a.work and a.source):
                sys.exit("--enqueue 엔 --work --source 필요")
            print("enqueued:", enqueue(conn, a.work, a.source, a.channel, a.topic, a.episode, a.max_shorts))
            return
        if a.list:
            with conn.cursor() as c:
                c.execute("select status, count(*) from public.gen_queue group by 1")
                print("status:", dict(c.fetchall()))
                c.execute("select work_title, status, run_id from public.gen_queue order by created_at desc limit 20")
                for r in c.fetchall():
                    print(f"  {r[1]:8} {r[0]}  run={r[2]}")
            return
        if not a.process:
            sys.exit("--enqueue / --process / --list 중 하나")

        wt = os.environ.get("AI_VIDEO_WORKTREE", DEFAULT_WT)
        py = os.environ.get("AI_VIDEO_GEN_PY", DEFAULT_PY)
        jobs = fetch_pending(conn, a.limit)
        print(f"[autogen] pending {len(jobs)}개 처리 (worktree={wt})")
        for job in jobs:
            cmd = build_gen_cmd(job, py, wt, no_research=not a.research, flags=flags)
            print(f"  RUN {job['work_title']}: {' '.join(cmd[:5])} … (source={job['source']})")
            if a.dry_run:
                continue
            set_status(conn, job["id"], "running")
            env = dict(os.environ, PYTHONPATH=wt,
                       AI_VIDEO_ROOT=os.environ.get("AI_VIDEO_ROOT", "/Users/gimsewon/rhoonart/ai-video"))
            try:
                r = subprocess.run(cmd, cwd=wt, env=env, capture_output=True, text=True, timeout=3600)
                jid = parse_job_id(r.stdout)
                if r.returncode == 0 and jid:
                    set_status(conn, job["id"], "done", run_id=jid)
                    print(f"    ✓ done run_id={jid}")
                else:
                    set_status(conn, job["id"], "failed", error=(r.stderr or r.stdout or "")[-400:])
                    print(f"    ✗ failed rc={r.returncode} jid={jid}")
            except Exception as e:  # noqa: BLE001
                set_status(conn, job["id"], "failed", error=str(e)[:400])
                print(f"    ✗ exception {type(e).__name__}: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
