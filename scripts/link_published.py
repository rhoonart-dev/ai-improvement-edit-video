#!/usr/bin/env python3
"""
T0-1b — 발행된 YouTube content_id를 인제스트된 auto_edit 클립에 연결.

흐름: 생성 → ingest_aivideo_run.py(content_id=NULL 적재) → 발행 → 여기서 content_id 연결.
clips.video_external_id / channel_id / published_at 를 채운다. 멱등.

주의(기획서 열린질문#1): 발행이 어디서/어떻게 일어나고 content_id가 언제 부여되는지는 미확정.
이 스크립트는 그 메커니즘과 무관하게 "content_id를 알면 연결"하는 함수/CLI다 — 발행 훅이 정해지면
그 지점에서 link_published()를 호출하면 된다.

환경변수: PIPELINE_DB_URL
실행:
  PIPELINE_DB_URL=... python scripts/link_published.py --run-id <job_id> [--short-label shorts_1] \
      --content-id <yt_video_id> [--channel <id|name>] [--published-at 2026-06-16T09:00:00+09:00]
  # 또는 직접 clip 지정: --clip-id <uuid> --content-id <yt>
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from ingest_aivideo_run import _connect, find_channel, find_existing_clip


# ─────────────────────────── pure helper (단위테스트 대상) ───────────────────────────

def parse_published_at(s):
    """ISO8601 → datetime|None. 빈 값이면 None, 형식 오류면 ValueError(호출부에서 처리)."""
    if not s:
        return None
    return datetime.fromisoformat(s)


# ─────────────────────────── DB ───────────────────────────

def resolve_clip_id(conn, *, clip_id, run_id, short_label):
    """대상 clip 결정: --clip-id 직접 또는 (run_id, short_label) 조회."""
    if clip_id:
        return clip_id
    if run_id:
        return find_existing_clip(conn, run_id, short_label)
    return None


def link_published(conn, clip_id, *, content_id, channel=None, published_at=None, commit=True):
    """clips 행에 발행 정보 연결. content_id가 다른 클립에 이미 연결돼 있으면 ValueError."""
    channel_id = find_channel(conn, channel) if channel else None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM public.clips WHERE video_external_id = %s AND id <> %s",
            (content_id, clip_id),
        )
        clash = cur.fetchone()
        if clash:
            raise ValueError(f"content_id {content_id!r} 는 이미 다른 clip({clash[0]})에 연결됨")
        cur.execute(
            """UPDATE public.clips
                 SET video_external_id = %s,
                     channel_id = COALESCE(%s, channel_id),
                     published_at = COALESCE(%s, published_at),
                     lifecycle_status = 'active'
               WHERE id = %s""",
            (content_id, channel_id, published_at, clip_id),
        )
        updated = cur.rowcount
    if commit:
        conn.commit()
    return updated


# ─────────────────────────── CLI ───────────────────────────

def main():
    ap = argparse.ArgumentParser(description="발행 content_id → auto_edit 클립 연결 (T0-1b)")
    ap.add_argument("--content-id", required=True, help="YouTube video id (→ clips.video_external_id)")
    ap.add_argument("--clip-id", help="대상 clip UUID(직접 지정)")
    ap.add_argument("--run-id", help="ai_video_run_id(job_id) — clip-id 없을 때 조회 키")
    ap.add_argument("--short-label", default=None, help="런이 여러 쇼츠를 낼 때 식별자(episode)")
    ap.add_argument("--channel", default=None, help="발행 채널 external id 또는 name")
    ap.add_argument("--published-at", default=None, help="ISO8601 발행시각(예: 2026-06-16T09:00:00+09:00)")
    ap.add_argument("--dry-run", action="store_true", help="DB 미접속, 의도만 출력")
    args = ap.parse_args()

    try:
        published_at = parse_published_at(args.published_at)
    except ValueError:
        sys.exit(f"--published-at 형식 오류(ISO8601 필요): {args.published_at!r}")

    if not args.clip_id and not args.run_id:
        sys.exit("--clip-id 또는 --run-id 중 하나는 필요합니다.")

    if args.dry_run:
        target = f"clip {args.clip_id}" if args.clip_id else f"run {args.run_id} / label {args.short_label}"
        print(f"[dry-run] content_id={args.content_id} → {target}, "
              f"channel={args.channel}, published_at={published_at}")
        return

    conn = _connect()
    try:
        clip_id = resolve_clip_id(conn, clip_id=args.clip_id, run_id=args.run_id,
                                  short_label=args.short_label)
        if not clip_id:
            sys.exit(f"대상 clip을 찾지 못함(run_id={args.run_id}, short_label={args.short_label}). "
                     "먼저 ingest_aivideo_run.py로 인제스트했는지 확인.")
        try:
            n = link_published(conn, clip_id, content_id=args.content_id,
                               channel=args.channel, published_at=published_at)
        except ValueError as e:
            sys.exit(str(e))
        print(f"linked clip {clip_id} → content_id={args.content_id} (rows={n})")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
