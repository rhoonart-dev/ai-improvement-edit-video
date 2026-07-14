#!/usr/bin/env python3
"""
T0-1c — youtube_studio 정합기: 수동 발행분을 auto_edit 클립에 매칭해 content_id 연결.

발행이 수동이라(기획서 열린질문#1) content_id 자동 캡처가 불가 → laeebly.youtube_studio에
2채널(스토리순삭/재미쇼츠) 영상 데이터가 적재되면, 미연결 auto_edit 클립과 매칭한다.

매칭 신호: video_title ↔ 클립 제목(주신호; youtube_studio video_title은 "후킹문구 #해시태그"
포맷이라 첫 '#' 앞 텍스트로 비교) + 작품(licensed/해시태그) + 길이(±tol). 채널로 후보 제한.
기본 dry-run(후보 제안). --apply 시 고신뢰·유일 매칭만 link_published로 연결(모호하면 사람에게).

환경변수: PIPELINE_DB_URL(읽기/쓰기), LAEEBLY_DB_URL(읽기)
실행:
  PIPELINE_DB_URL=.. LAEEBLY_DB_URL=.. python scripts/reconcile_published.py             # dry-run 제안
  ...                                  python scripts/reconcile_published.py --apply       # 고신뢰 연결
  ... python scripts/reconcile_published.py --probe-title "..." --probe-work "..." --probe-duration 50  # 매칭 테스트(실 후보)
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from difflib import SequenceMatcher

from etl_transforms import parse_len_sec

import channel_registry as registry  # 대상 채널 목록 단일 소스(config/channels.json)

AUTO_THRESHOLD = 0.75      # 이 이상 + 2위와 마진이면 자동 연결 후보
MARGIN = 0.15              # 1·2위 점수 차가 이 이상이어야 '유일'로 인정
MIN_SHOW = 0.40            # 이 미만 후보는 표시 안 함
DURATION_TOL_SEC = 4.0

_KEEP = re.compile(r"[^0-9a-z가-힣\s]")


# ─────────────────────────── 순수 매칭 (단위테스트 대상) ───────────────────────────

def normalize_title(s):
    """제목 정규화: 첫 '#' 이후(해시태그 꼬리) 제거 → 개행→공백 → 소문자 → 특수문자 제거 → 공백정리."""
    if not s:
        return ""
    head = s.split("#", 1)[0]
    head = head.replace("\n", " ").replace("\t", " ").lower()
    head = _KEEP.sub(" ", head)
    return " ".join(head.split())


def _tokens(norm):
    return set(norm.split())


def title_similarity(a, b):
    """정규화 제목 유사도 = 0.6*시퀀스비율 + 0.4*토큰 자카드 (0~1)."""
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    seq = SequenceMatcher(None, na, nb).ratio()
    ta, tb = _tokens(na), _tokens(nb)
    jac = len(ta & tb) / len(ta | tb) if (ta or tb) else 0.0
    return 0.6 * seq + 0.4 * jac


def duration_score(clip_dur, video_len_raw, tol=DURATION_TOL_SEC):
    """길이 근접도 0~1. 한쪽이라도 없으면 0.5(중립)."""
    b = parse_len_sec(video_len_raw)
    if clip_dur is None or b is None:
        return 0.5
    return max(0.0, 1.0 - abs(float(clip_dur) - b) / tol)


def work_match(clip_work, video_title, licensed_title):
    """작품 일치: 공백 제거 정규화 작품명이 영상제목(해시태그 포함) 또는 licensed_title에 포함?"""
    cw = normalize_title(clip_work).replace(" ", "")
    if not cw:
        return False
    hay = (normalize_title(video_title) + normalize_title(licensed_title)).replace(" ", "")
    # 해시태그 안의 작품명도 잡으려면 원문도 본다(normalize는 '#'앞만 보므로):
    raw = ((video_title or "") + (licensed_title or "")).lower().replace(" ", "")
    raw = _KEEP.sub("", raw)
    return cw in hay or cw in raw


def score_candidate(clip, video):
    """clip(title, work_title, duration_sec) × video(video_title, licensed_video_title, video_length, content_id)
       → score dict. score = 0.55*title + 0.25*duration + 0.20*work."""
    t = title_similarity(clip.get("title"), video.get("video_title"))
    d = duration_score(clip.get("duration_sec"), video.get("video_length"))
    w = work_match(clip.get("work_title"), video.get("video_title"), video.get("licensed_video_title"))
    score = 0.55 * t + 0.25 * d + 0.20 * (1.0 if w else 0.0)
    return {"content_id": video.get("content_id"), "score": round(score, 4),
            "title_sim": round(t, 4), "dur_score": round(d, 4), "work_ok": bool(w),
            "video_title": video.get("video_title")}


def rank_candidates(clip, videos):
    return sorted((score_candidate(clip, v) for v in videos), key=lambda x: x["score"], reverse=True)


def decide(ranked, auto_threshold=AUTO_THRESHOLD, margin=MARGIN, min_show=MIN_SHOW):
    """('auto'|'ambiguous'|'none', 표시할 후보). auto = 고신뢰 + 유일(2위와 마진)."""
    shown = [c for c in ranked if c["score"] >= min_show]
    if not shown:
        return "none", []
    top = shown[0]
    if top["score"] >= auto_threshold and (len(shown) == 1 or top["score"] - shown[1]["score"] >= margin):
        return "auto", shown
    return "ambiguous", shown[:5]


# ─────────────────────────── DB (psycopg lazy) ───────────────────────────

def fetch_unlinked_clips(pconn):
    from psycopg.rows import dict_row
    with pconn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            select c.id as clip_id, c.duration_sec, c.created_at,
                   w.title  as work_title,
                   ch.name  as channel_name,
                   (m.edit_plan->'layout'->>'top_title') as title
            from clips c
            left join clip_metadata m on m.clip_id = c.id
            left join works w        on w.id  = c.work_id
            left join channels ch    on ch.id = c.channel_id
            where c.source = 'auto_edit' and c.video_external_id is null
              and c.lifecycle_status = 'active'
            order by c.created_at
        """)
        return cur.fetchall()


def fetch_linked_content_ids(pconn):
    with pconn.cursor() as cur:
        cur.execute("select video_external_id from clips where video_external_id is not null")
        return {r[0] for r in cur.fetchall()}


def fetch_candidate_videos(lconn, since_days=120):
    from psycopg.rows import dict_row
    with lconn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            select distinct on (btrim(content_id))
                   btrim(content_id) as content_id, channel_name, video_title,
                   licensed_video_title, video_length, publish_time
            from public.youtube_studio
            where channel_name = any(%s)
              and content_id is not null and btrim(content_id) <> ''
              and upload_at >= now() - make_interval(days => %s)
            order by btrim(content_id), upload_at desc
        """, (list(registry.channel_names()), since_days))
        return cur.fetchall()


def _fmt(c):
    return (f"   {c['score']:.3f}  t={c['title_sim']:.2f} d={c['dur_score']:.2f} w={c['work_ok']}  "
            f"{c['content_id']} | {(c['video_title'] or '')[:55]}")


# ─────────────────────────── CLI ───────────────────────────

def main():
    ap = argparse.ArgumentParser(description="youtube_studio 정합기 (T0-1c)")
    ap.add_argument("--apply", action="store_true", help="고신뢰·유일 매칭을 실제 연결(link_published)")
    ap.add_argument("--since-days", type=int, default=120, help="후보로 볼 발행 영상의 최근 일수")
    ap.add_argument("--probe-title", help="가상 클립 제목으로 실 후보 매칭 테스트(DB 쓰기 없음)")
    ap.add_argument("--probe-work", default=None)
    ap.add_argument("--probe-duration", type=float, default=None)
    args = ap.parse_args()

    import psycopg

    if args.probe_title:
        lurl = os.environ.get("LAEEBLY_DB_URL") or sys.exit("LAEEBLY_DB_URL 미설정")
        lconn = psycopg.connect(lurl)
        try:
            vids = fetch_candidate_videos(lconn, args.since_days)
        finally:
            lconn.close()
        clip = {"title": args.probe_title, "work_title": args.probe_work, "duration_sec": args.probe_duration}
        ranked = rank_candidates(clip, vids)
        dec, _ = decide(ranked)
        print(f"[probe] candidates={len(vids)}  decision={dec}")
        for c in ranked[:8]:
            print(_fmt(c))
        return

    purl = os.environ.get("PIPELINE_DB_URL") or sys.exit("PIPELINE_DB_URL 미설정")
    lurl = os.environ.get("LAEEBLY_DB_URL") or sys.exit("LAEEBLY_DB_URL 미설정")
    pconn = psycopg.connect(purl)
    lconn = psycopg.connect(lurl)
    try:
        clips = fetch_unlinked_clips(pconn)
        linked = fetch_linked_content_ids(pconn)
        vids = [v for v in fetch_candidate_videos(lconn, args.since_days) if v["content_id"] not in linked]
        print(f"unlinked auto_edit clips: {len(clips)} | candidate videos: {len(vids)} (apply={args.apply})")
        if not clips:
            print("연결할 auto_edit 클립 없음(아직 인제스트/발행 전).")
            return
        from link_published import link_published
        for clip in clips:
            pool = [v for v in vids if (not clip.get("channel_name")) or v["channel_name"] == clip["channel_name"]]
            ranked = rank_candidates(clip, pool)
            dec, shown = decide(ranked)
            print(f"\nclip {clip['clip_id']}  work={clip['work_title']!r} dur={clip['duration_sec']} "
                  f"title={(clip['title'] or '')[:40]!r}  → {dec}")
            for c in shown:
                print(_fmt(c))
            if dec == "auto" and args.apply:
                top = shown[0]
                n = link_published(pconn, clip["clip_id"], content_id=top["content_id"],
                                   channel=clip.get("channel_name"))
                print(f"   → LINKED content_id={top['content_id']} (rows={n})")
                vids = [v for v in vids if v["content_id"] != top["content_id"]]
            elif dec == "auto":
                print("   (--apply 시 자동 연결)")
    finally:
        pconn.close()
        lconn.close()


if __name__ == "__main__":
    main()
