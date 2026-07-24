#!/usr/bin/env python3
"""발행 자동화 — YouTube Data API v3로 쇼츠 업로드 → content_id → link_published.

안전(plan §9, 비가역 액션): ① 안전 게이트(judge 안전판정 존재 + 환각無; quality는 성과예측 아님)
② --publish opt-in(기본 dry-run) ③ 기본 privacy=private. 자동 발행이라도 이 가드를 통과해야만 실제 업로드.

⚠️ OAuth 자격증명 필요(채널 업로드 권한):
   env YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN_<CHANNELSLUG> (채널별 전용 토큰만.
   §3-5: generic YT_REFRESH_TOKEN 폴백 제거 — 미등록 채널·토큰 미설정은 하드 실패 = 오채널 차단.)
deps: google-api-python-client google-auth google-auth-oauthlib
env: PIPELINE_DB_URL, YT_*
실행:
  dry-run: ... publish_youtube.py --clip-id <uuid> --video <local.mp4> --channel 스토리순삭
  실제:    ... publish_youtube.py --clip-id <uuid> --video <local.mp4> --channel 스토리순삭 --publish [--privacy unlisted]
"""
from __future__ import annotations

import argparse
import os
import re
import sys

try:
    from envload import load_env
except ImportError:  # 단독 import 컨텍스트
    def load_env(*a, **k):
        return {}

import channel_registry as registry  # 채널→토큰/OAuth 매핑 단일 소스(config/channels.json)

CATEGORY_ENTERTAINMENT = "24"
UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"


# ─────────────────────────── 순수 (단위테스트) ───────────────────────────

def token_env_name(channel):
    """채널 표시명 → YT refresh token env 변수명. 레지스트리(config/channels.json)로 해석.
    §3-5 오채널 업로드 차단: 미등록 채널(token_slug 없음)은 generic 폴백 없이 하드 실패 —
    잘못된 채널명으로 '아무 채널'에 업로드되는 사고를 기계적으로 차단(레지스트리의 generic
    폴백을 여기서 봉쇄)."""
    rec = registry.resolve(channel)
    if not rec or not rec.get("token_slug"):
        raise ValueError(f"미등록 채널 {channel!r} — config/channels.json 에 등록된 채널만 업로드 가능")
    return f"YT_REFRESH_TOKEN_{rec['token_slug']}"


def build_snippet(title, hashtags=None, category=CATEGORY_ENTERTAINMENT):
    """YouTube snippet — 제목(개행→공백, ≤100자) + 설명(해시태그) + tags(≤15). 순수.
    해시태그 내 공백은 언더바 치환 — 공백이 있으면 YouTube가 첫 단어까지만 태그로 인식."""
    t = " ".join((title or "").split())[:100]
    tags = [h.lstrip("#").strip() for h in (hashtags or []) if h and h.strip()]
    desc = " ".join("#" + "_".join(x.split()) for x in tags) if tags else ""
    return {"title": t or "shorts", "description": desc, "tags": tags[:15], "categoryId": category}


# ─────────────────────────── DB / 게이트 ───────────────────────────

def gate_ok(conn, clip_id, safety_floor=None):
    """발행 *안전* 게이트: 최신 judge 안전판정 존재 AND 환각無 (+ opt-in safety_floor 미만 차단).
    judge quality 는 성과예측이 아니므로 성과 바(0.6 등)로 막지 않는다 — 명백히 깨진 것만 거르고,
    '잘 될 것'은 발행 후 벤치마크/+14일이 판정. (judge 자체가 없으면 안전미확인 → 차단.)"""
    with conn.cursor() as c:
        c.execute("""select quality_score, rubric_scores->>'hallucination_flag'
                     from public.judge_runs where clip_id=%s order by created_at desc limit 1""", (clip_id,))
        r = c.fetchone()
    if not r or r[0] is None:
        return False, "judge 안전판정 없음"
    if str(r[1]).lower() == "true":
        return False, "환각 플래그(안전)"
    if safety_floor is not None and float(r[0]) < safety_floor:
        return False, f"quality {r[0]} < 안전바닥 {safety_floor}(명백히 깨짐)"
    return True, f"안전(환각無, quality={r[0]})"


def fetch_clip_title(conn, clip_id):
    with conn.cursor() as c:
        c.execute("""select m.edit_plan->'layout'->>'top_title', w.title
                     from public.clip_metadata m join public.clips c on c.id=m.clip_id
                     left join public.works w on w.id=c.work_id where m.clip_id=%s""", (clip_id,))
        r = c.fetchone()
    return (r[0], r[1]) if r else (None, None)


# ─────────────────────────── YouTube 업로드 ───────────────────────────

def _credentials(channel):
    """채널별 refresh token + (프로젝트 분리) 프로젝트별 OAuth 클라이언트로 Credentials 조립.
    OAuth 클라이언트(앱)는 gcp_project 별(DEFAULT면 전역 YT_CLIENT_ID/SECRET) — 앱은 채널 정체성이
    아니라 폴백 OK. 단 채널을 식별하는 **refresh token 은 채널별만**(generic 폴백 제거, §3-5) —
    token_env_name 이 미등록 채널을 하드 실패시키므로 여기 도달하면 등록 채널이 보장됨."""
    from google.oauth2.credentials import Credentials
    rec = registry.resolve(channel)
    cid_key, cs_key = registry.client_env_names(rec.get("gcp_project") if rec else None)
    cid = os.environ.get(cid_key) or os.environ.get("YT_CLIENT_ID")
    cs = os.environ.get(cs_key) or os.environ.get("YT_CLIENT_SECRET")
    rt = os.environ.get(token_env_name(channel))   # §3-5: generic 폴백 없음(채널별 토큰만)
    if not (cid and cs and rt):
        return None
    return Credentials(None, refresh_token=rt, client_id=cid, client_secret=cs,
                       token_uri="https://oauth2.googleapis.com/token", scopes=[UPLOAD_SCOPE])


def upload(video_path, snippet, privacy, channel):
    """YouTube videos.insert → content_id. OAuth 미설정이면 RuntimeError."""
    creds = _credentials(channel)
    if creds is None:
        raise RuntimeError("YouTube OAuth 미설정 — YT_CLIENT_ID/YT_CLIENT_SECRET/YT_REFRESH_TOKEN 필요")
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    yt = build("youtube", "v3", credentials=creds)
    body = {"snippet": snippet, "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    resp = yt.videos().insert(part="snippet,status", body=body, media_body=media).execute()
    return resp["id"]


def main():
    load_env()  # repo .env 자동 로드(YT_*, PIPELINE_DB_URL 등) — 이미 설정된 값은 보존
    ap = argparse.ArgumentParser(description="발행 자동화 (YouTube 업로드 → link)")
    ap.add_argument("--clip-id", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--channel", required=True)
    ap.add_argument("--title")
    ap.add_argument("--hashtags", nargs="*")
    ap.add_argument("--safety-floor", type=float, default=None,
                    help="명백히 깨진 산출물 차단용 안전 바닥. 미지정=judge quality로 안 막음(성과예측 아님)")
    ap.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    ap.add_argument("--publish", action="store_true", help="실제 업로드(기본 dry-run)")
    a = ap.parse_args()

    import psycopg
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    try:
        title = a.title
        work = None
        if not title:
            title, work = fetch_clip_title(conn, a.clip_id)
        hashtags = a.hashtags or ([work] if work else [])
        snip = build_snippet(title, hashtags)
        ok, reason = gate_ok(conn, a.clip_id, a.safety_floor)
        print(f"gate: {'PASS' if ok else 'BLOCK'} ({reason}) | title={snip['title']!r} privacy={a.privacy} oauth={'O' if _credentials(a.channel) else 'X(미설정)'}")
        if not a.publish:
            print("[dry-run] --publish 시 실제 업로드. 안전: 게이트 통과 + opt-in 필요.")
            return
        if not ok:
            sys.exit(f"게이트 차단 — 발행 안 함: {reason}")
        vid = upload(a.video, snip, a.privacy, a.channel)
        print("uploaded content_id:", vid)
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from datetime import datetime, timezone
        from link_published import link_published
        now = datetime.now(timezone.utc)
        # published_at 을 업로드 순간으로 기록 — R5(§4-3)·판정 창 계산의 근거.
        # (laeebly ETL 이 이후 자체 publish_time 으로 백필하지만, 등록 시점엔 이 값이 유일.)
        # snippet — 실제 발행된 형태를 provenance 로 기록(§3-8).
        snippet = {**snip, "channel": a.channel, "privacy": a.privacy,
                   "published_at": now.isoformat()}
        n = link_published(conn, a.clip_id, content_id=vid, channel=a.channel,
                           published_at=now, snippet=snippet)
        print(f"linked clip {a.clip_id} → {vid} (rows={n})")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
