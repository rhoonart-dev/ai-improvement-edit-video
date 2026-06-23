#!/usr/bin/env python3
"""
T0-1 인제스트 — ai-video run 출력 → 격리 파이프라인 DB
  clips(source='auto_edit') + clip_metadata(provenance) 적재.

기획서 Phase 0 / T0-1. 생성↔성과 루프의 "최대 공백"(clip_metadata=0, auto_edit=0)을 닫는다.
완료기준: 임의 쇼츠 1개 → run/edit_plan/config로 역추적 가능.

설계:
- ETL 컨벤션 재사용(etl_laeebly_to_pipeline.py): psycopg + PIPELINE_DB_URL, works=존재확인 후 insert.
- 사람 클립은 source='existing'(ETL), ai-video 산출물은 source='auto_edit'(여기).
- 발행 전이라 video_external_id(YouTube content_id)는 NULL 가능 → 발행 후 채움(T0-1b: link_published).
- 멱등: (ai_video_run_id, episode=short_label) 키로 clip_metadata를 조회해 재실행 시 갱신.

Provenance 계약  ── T0-2가 ai-video run_log["provenance"](또는 run-dir/provenance.json)에 채워야 할 것:
  {
    "git_sha":          "<ai-video HEAD sha at run time>",
    "models":           {"pro": "gemini-3.1-pro-preview", "flash": "gemini-3-flash-preview"},
    "config":           {"app": <asdict(AppConfig)>, "design": <asdict(DesignConfig)>},
    "prompt_set_hash":  "<sha256 of prompt templates>",
    "prompt_versions":  {"<name>": "<hash/ver>", ...},
    "reranker_version": "<str|null>"
  }
provenance가 없으면(T0-2 전): git_sha는 AI_VIDEO_ROOT 현재 HEAD로 best-effort,
config_hash/prompt_versions는 NULL(런 시점 config를 사후 복원할 수 없으므로) + 경고 출력.

환경변수: PIPELINE_DB_URL(쓰기), AI_VIDEO_ROOT(git_sha 폴백; 기본 /Users/gimsewon/rhoonart/ai-video)
실행:
  PIPELINE_DB_URL=... python scripts/ingest_aivideo_run.py --run-dir <outputs/<job_id>> \
      [--short-label shorts_1] [--channel <yt channel id|name>] [--content-id <yt id>] [--exploration]
  # DB 없이 매핑만 확인:
  python scripts/ingest_aivideo_run.py --run-dir <dir> --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from etl_transforms import is_short

DEFAULT_AI_VIDEO_ROOT = "/Users/gimsewon/rhoonart/ai-video"
INGEST_SOURCE = "ai_video"


# ─────────────────────────── pure helpers (DB 무관 · 단위테스트 대상) ───────────────────────────

def canonical_json(obj) -> str:
    """해시용 정규화 JSON(키 정렬, 공백 제거)."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_json(path: Path):
    """존재하지 않거나 깨졌으면 None."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def timeline_span(edit_plan: dict):
    """edit_plan.timeline → (origin_start_sec, origin_end_sec, duration_sec).
    duration = 합성된 쇼츠 길이(세그먼트 길이 합), origin_* = 소스 구간의 최소/최대(거친 범위;
    비연속 다구간일 수 있어 전체 타임라인은 clip_metadata.edit_plan jsonb에 보존)."""
    tl = (edit_plan or {}).get("timeline") or []
    segs = []
    for c in tl:
        s, e = c.get("clip_start_sec"), c.get("clip_end_sec")
        if s is not None and e is not None:
            segs.append((float(s), float(e)))
    if not segs:
        return None, None, None
    duration = sum(max(0.0, e - s) for s, e in segs)
    return min(s for s, _ in segs), max(e for _, e in segs), duration


def git_head_sha(root: str):
    """AI_VIDEO_ROOT의 현재 HEAD sha(폴백용). 실패 시 None.
    주의: '현재' HEAD라 인제스트를 런 직후 했을 때만 런 시점과 일치 — 정확도는 T0-2가 보장."""
    try:
        out = subprocess.run(
            ["git", "-C", root, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def extract_provenance(run_log, run_dir: Path, ai_video_root: str) -> dict:
    """run_log['provenance'] 또는 run-dir/provenance.json 에서 provenance 추출(+git_sha 폴백)."""
    prov = {}
    if isinstance(run_log, dict) and isinstance(run_log.get("provenance"), dict):
        prov = dict(run_log["provenance"])
    else:
        pj = load_json(run_dir / "provenance.json")
        if isinstance(pj, dict):
            prov = pj
    config_snap = prov.get("config")
    return {
        "git_sha": prov.get("git_sha") or git_head_sha(ai_video_root),
        "config": config_snap,
        "config_hash": sha256_hex(canonical_json(config_snap)) if config_snap else None,
        "prompt_versions": prov.get("prompt_versions"),
        "reranker_version": prov.get("reranker_version"),
        "complete": bool(config_snap),   # config 스냅샷이 있어야 완전(T0-2)
    }


def build_rows(edit_plan, run_log, run_dir: Path, *, short_label, content_id,
               is_exploration, ai_video_root):
    """edit_plan/run_log → (clip_columns, metadata_columns, provenance_info). 순수 함수."""
    inp = (edit_plan or {}).get("input") or (run_log or {}).get("input") or {}
    o_start, o_end, duration = timeline_span(edit_plan)
    prov = extract_provenance(run_log, run_dir, ai_video_root)

    clip = {                                   # public.clips 컬럼만
        "work_title": inp.get("work_title"),
        "episode": short_label,                # 멱등 라벨(런이 여러 쇼츠 낼 때)
        "source": "auto_edit",
        "origin_start_sec": o_start,
        "origin_end_sec": o_end,
        "duration_sec": duration,
        "video_external_id": content_id,       # 발행 전 None
        "is_format_short": is_short(duration) if duration is not None else True,
        "is_exploration": is_exploration,
    }
    metadata = {                               # public.clip_metadata 컬럼
        "ai_video_run_id": (run_log or {}).get("job_id"),
        "git_sha": prov["git_sha"],
        "config_hash": prov["config_hash"],
        "prompt_versions": prov["prompt_versions"],
        "reranker_version": prov["reranker_version"],
        "edit_plan": edit_plan,
        "checkpoint_story": load_json(run_dir / "checkpoint_story.json"),
        "run_log": run_log,
        "ingest_source": INGEST_SOURCE,
    }
    return clip, metadata, prov


# ─────────────────────────── DB (psycopg lazy import) ───────────────────────────

def _connect():
    import psycopg
    url = os.environ.get("PIPELINE_DB_URL")
    if not url:
        sys.exit("PIPELINE_DB_URL 미설정 — 격리 파이프라인 DB 연결문자열이 필요합니다(--dry-run으로 우회 가능).")
    return psycopg.connect(url)


def upsert_work(conn, title):
    """works.title 기준 존재확인 후 insert(ETL 컨벤션). title 없으면 None."""
    if not title:
        return None
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM public.works WHERE title=%s", (title,))
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute("INSERT INTO public.works (title, content_type) VALUES (%s, NULL) RETURNING id", (title,))
        return cur.fetchone()[0]


def find_channel(conn, ref):
    """external id 또는 name으로 channel 찾기(없으면 None — 발행 전엔 채널 미상 허용)."""
    if not ref:
        return None
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id FROM public.channels
               WHERE platform='youtube' AND (channel_external_id=%s OR name=%s) LIMIT 1""",
            (ref, ref),
        )
        row = cur.fetchone()
        return row[0] if row else None


def find_existing_clip(conn, run_id, short_label):
    """(ai_video_run_id, episode) 멱등 키로 기존 auto_edit 클립 찾기."""
    if not run_id:
        return None
    with conn.cursor() as cur:
        cur.execute(
            """SELECT c.id FROM public.clips c JOIN public.clip_metadata m ON m.clip_id = c.id
               WHERE m.ai_video_run_id = %s AND c.source = 'auto_edit'
                 AND c.episode IS NOT DISTINCT FROM %s LIMIT 1""",
            (run_id, short_label),
        )
        row = cur.fetchone()
        return row[0] if row else None


def write_rows(conn, clip, metadata, *, work_id, channel_id, existing_clip_id, commit=True):
    from psycopg.types.json import Json

    def jb(v):
        return Json(v) if v is not None else None

    with conn.cursor() as cur:
        if existing_clip_id:
            clip_id = existing_clip_id
            cur.execute(
                """UPDATE public.clips
                     SET work_id = %s,
                         channel_id = COALESCE(%s, channel_id),
                         origin_start_sec = %s, origin_end_sec = %s, duration_sec = %s,
                         video_external_id = COALESCE(%s, video_external_id),
                         is_format_short = %s, is_exploration = %s
                   WHERE id = %s""",
                (work_id, channel_id, clip["origin_start_sec"], clip["origin_end_sec"],
                 clip["duration_sec"], clip["video_external_id"], clip["is_format_short"],
                 clip["is_exploration"], clip_id),
            )
        else:
            cur.execute(
                """INSERT INTO public.clips
                     (work_id, channel_id, episode, source, origin_start_sec, origin_end_sec,
                      duration_sec, video_external_id, is_format_short, is_exploration)
                   VALUES (%s, %s, %s, 'auto_edit', %s, %s, %s, %s, %s, %s) RETURNING id""",
                (work_id, channel_id, clip["episode"], clip["origin_start_sec"], clip["origin_end_sec"],
                 clip["duration_sec"], clip["video_external_id"], clip["is_format_short"],
                 clip["is_exploration"]),
            )
            clip_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO public.clip_metadata
                 (clip_id, ai_video_run_id, git_sha, config_hash, prompt_versions,
                  reranker_version, edit_plan, checkpoint_story, run_log, ingest_source)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (clip_id) DO UPDATE SET
                 ai_video_run_id = EXCLUDED.ai_video_run_id, git_sha = EXCLUDED.git_sha,
                 config_hash = EXCLUDED.config_hash, prompt_versions = EXCLUDED.prompt_versions,
                 reranker_version = EXCLUDED.reranker_version, edit_plan = EXCLUDED.edit_plan,
                 checkpoint_story = EXCLUDED.checkpoint_story, run_log = EXCLUDED.run_log,
                 ingest_source = EXCLUDED.ingest_source""",
            (clip_id, metadata["ai_video_run_id"], metadata["git_sha"], metadata["config_hash"],
             jb(metadata["prompt_versions"]), metadata["reranker_version"],
             jb(metadata["edit_plan"]), jb(metadata["checkpoint_story"]), jb(metadata["run_log"]),
             metadata["ingest_source"]),
        )
    if commit:
        conn.commit()
    return clip_id


# ─────────────────────────── CLI ───────────────────────────

def main():
    ap = argparse.ArgumentParser(description="ai-video run → clips/clip_metadata 인제스트 (T0-1)")
    ap.add_argument("--run-dir", required=True, help="ai-video outputs/<job_id> 디렉토리")
    ap.add_argument("--edit-plan", help="edit_plan.json 경로(기본 <run-dir>/edit_plan.json)")
    ap.add_argument("--run-log", help="run_log.json 경로(기본 <run-dir>/run_log.json)")
    ap.add_argument("--short-label", default=None, help="런이 여러 쇼츠를 낼 때 식별자(멱등 키)")
    ap.add_argument("--channel", default=None, help="발행 채널 external id 또는 name(있으면 연결)")
    ap.add_argument("--content-id", default=None, help="이미 발행됐다면 YouTube content_id")
    ap.add_argument("--exploration", action="store_true", help="챌린저/탐색 클립 표시")
    ap.add_argument("--ai-video-root", default=os.environ.get("AI_VIDEO_ROOT", DEFAULT_AI_VIDEO_ROOT))
    ap.add_argument("--dry-run", action="store_true", help="DB 미접속, 매핑 결과만 출력")
    args = ap.parse_args()

    run_dir = Path(args.run_dir)
    edit_plan = load_json(Path(args.edit_plan) if args.edit_plan else run_dir / "edit_plan.json")
    run_log = load_json(Path(args.run_log) if args.run_log else run_dir / "run_log.json")
    if edit_plan is None:
        sys.exit(f"edit_plan.json 을 찾을 수 없음: {run_dir}")

    clip, metadata, prov = build_rows(
        edit_plan, run_log, run_dir,
        short_label=args.short_label, content_id=args.content_id,
        is_exploration=args.exploration, ai_video_root=args.ai_video_root,
    )
    if not prov["complete"]:
        print(
            "⚠️  provenance 불완전: run_log에 'provenance'(config 스냅샷)가 없음 → "
            "config_hash/prompt_versions=NULL. T0-2(ai-video 스탬핑) 적용 후 자동 완전. "
            f"git_sha 폴백={'있음' if prov['git_sha'] else '없음'}",
            file=sys.stderr,
        )

    if args.dry_run:
        preview = dict(metadata)
        for big in ("edit_plan", "run_log", "checkpoint_story"):
            preview[big] = f"<{type(metadata[big]).__name__ if metadata[big] is not None else 'None'}>"
        print(json.dumps(
            {"clip": clip, "metadata": preview, "provenance_complete": prov["complete"]},
            ensure_ascii=False, indent=2,
        ))
        return

    conn = _connect()
    try:
        work_id = upsert_work(conn, clip["work_title"])
        channel_id = find_channel(conn, args.channel)
        existing = find_existing_clip(conn, metadata["ai_video_run_id"], clip["episode"])
        clip_id = write_rows(conn, clip, metadata, work_id=work_id,
                             channel_id=channel_id, existing_clip_id=existing)
        print(f"{'updated' if existing else 'inserted'} clip {clip_id} "
              f"(run={metadata['ai_video_run_id']}, work={clip['work_title']!r}, "
              f"dur={clip['duration_sec']}, content_id={clip['video_external_id']})")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
