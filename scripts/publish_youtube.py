#!/usr/bin/env python3
"""발행 자동화 — YouTube Data API v3로 쇼츠 업로드 → content_id → link_published.

안전(plan §9, 비가역 액션): ① 안전 게이트(judge 안전판정 존재 + 환각無; quality는 성과예측 아님)
② --publish opt-in(기본 dry-run) ③ 기본 privacy=private. 자동 발행이라도 이 가드를 통과해야만 실제 업로드.

⚠️ OAuth 자격증명 필요(채널 업로드 권한):
   env YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN_<CHANNELSLUG> (채널별 전용 토큰만.
   §3-5: generic YT_REFRESH_TOKEN 폴백 제거 — 미등록 채널·토큰 미설정은 하드 실패 = 오채널 차단.)
deps: google-api-python-client google-auth google-auth-oauthlib
env: PIPELINE_DB_URL, YT_*, LAEEBLY_DB_URL(작품 식별코드 해시태그 조회 — 없으면 경고 후 생략)
실행:
  dry-run: ... publish_youtube.py --clip-id <uuid> --video <local.mp4> --channel 스토리순삭
  실제:    ... publish_youtube.py --clip-id <uuid> --video <local.mp4> --channel 스토리순삭 --publish [--privacy unlisted]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

try:
    from envload import load_env
except ImportError:  # 단독 import 컨텍스트
    def load_env(*a, **k):
        return {}

import channel_registry as registry  # 채널→토큰/OAuth 매핑 단일 소스(config/channels.json)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
NOTICE_CONFIG_PATH = REPO_ROOT / "config" / "work_publish_notice.json"
CATEGORY_ENTERTAINMENT = "24"
UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
# ⚠️ Credentials(scopes=…) 를 지정하면 **그 부분집합으로 좁혀진 access token** 이 발급된다
# (google-auth refresh_grant: "Scopes to request. If present, all scopes must be authorized").
# 그래서 여기서 scope 를 선언하지 않는다 — 토큰이 실제로 받은 권한 그대로 쓴다. 이유 2가지:
#  ① 좁히면 업로드 외 호출이 죽는다. upload 만 선언한 자격증명을 공개 전환 코드가 재사용해
#     videos.list(part=status) 까지 403 insufficientPermissions 로 죽었다(2026-07-27 밤 전 채널).
#  ② 넓게 선언해도 못 고친다 — 미승인 scope 를 요청하면 갱신 자체가 거부돼 지금 되는 업로드까지
#     막힌다. 권한은 발급 시점 동의에 고정이라 코드로 넓힐 수 없다(scripts/get_youtube_token.py).
# 토큰이 실제로 무슨 권한인지는 scripts/check_youtube_scopes.py 로 확인한다.


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


def hashtag_body(text):
    """작품명 → 해시태그 본문. 공백은 언더바 치환(공백이 있으면 YouTube가 첫 단어까지만 태그로 인식),
    콜론·마침표 등 특수문자는 제거(태그가 그 지점에서 끊겨 '샤먼: 미신전'이 '#샤먼'이 되던 버그). 순수."""
    return "_".join(re.sub(r"[^\w\s]", "", text or "", flags=re.UNICODE).split())


def build_snippet(title, hashtags=None, category=CATEGORY_ENTERTAINMENT,
                  work_title=None, episode=None, work_hashtags=None,
                  work_display=None, notice_lines=None):
    """YouTube snippet — 제목(개행→공백, ≤100자) + 설명 + tags(≤15). 순수.

    설명 = "<작품표기> <N>화" 한 줄 (+ 필수 표기 줄들) + 빈 줄 + 해시태그 줄.
    - work_display: 첫 줄에서 작품명 대신 쓸 표기. 권리사가 특정 문구를 요구할 때 사용
      (예: 샤먼 → "티빙 오리지널 [샤먼: 미신전]"). 미지정 시 work_title, 그것도 없으면 첫 해시태그.
      ⚠️ 유튜브 설명란은 꺾쇠(<>)를 못 쓰므로 대괄호로 표기한다.
    - notice_lines: 첫 줄 아래에 그대로 넣을 필수 표기 줄들.
    - episode 가 None 이어도 필수 표기는 남아야 하므로 work_display/notice_lines 는 출력된다.
    - work_hashtags: 작품 식별코드 등 laeebly 요구 해시태그 — 해시태그 줄 뒤에 붙는다(중복 제거).
      YouTube tags 에는 넣지 않는다(설명란 표기 요구사항이라)."""
    t = " ".join((title or "").split())[:100]
    tags = [h.lstrip("#").strip() for h in (hashtags or []) if h and h.strip()]
    bodies = [hashtag_body(x) for x in tags]
    for w in (work_hashtags or []):
        b = hashtag_body(str(w).lstrip("#"))
        if b and b not in bodies:
            bodies.append(b)
    tag_line = " ".join("#" + b for b in bodies if b)

    lines = []
    work = " ".join((work_display or work_title or (tags[0] if tags else "")).split())
    if episode is not None and work:
        lines.append(f"{work} {episode}화")
    elif work_display:                 # 회차 미상이어도 권리사 필수 표기는 빠지면 안 된다
        lines.append(work)
    for ln in (notice_lines or []):
        if str(ln).strip():
            lines.append(str(ln).strip())
    head = "\n".join(lines)
    desc = f"{head}\n\n{tag_line}".strip() if head else tag_line
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


def parse_hashtags(text):
    """'#o483K #예능' 같은 문자열 → ['o483K', '예능'] (# 없는 토큰도 허용). 순수."""
    return [tok.lstrip("#").strip() for tok in re.split(r"[\s,]+", text or "") if tok.strip()]


# 권리사 가이드가 '설명란 표기'를 요구하는지 감지하는 힌트(추출이 아니라 **경고용**).
NOTICE_HINT_RE = re.compile(r"(설명란|더보기란)[^가-힣]{0,40}(표기|명시|기재)|표기\s*필수")


def guide_requires_notice(guide_html):
    """권리사 가이드가 설명란 표기를 요구하는 것으로 보이는가. 순수.

    guide 는 산문 HTML 이라 문구를 기계적으로 추출하면 오히려 위험하다. 그래서 여기서는
    '요구가 있어 보이는데 설정이 비어 있음'을 경고하는 용도로만 쓴다(자동 삽입 안 함)."""
    if not guide_html:
        return False
    return bool(NOTICE_HINT_RE.search(re.sub(r"<[^>]+>", " ", guide_html)))


def load_notice_config(path=NOTICE_CONFIG_PATH):
    """config/work_publish_notice.json → {작품명: {...}}. 파일이 없거나 깨지면 {} (발행은 계속)."""
    try:
        return (json.loads(pathlib.Path(path).read_text(encoding="utf-8")) or {}).get("works") or {}
    except (OSError, json.JSONDecodeError):
        return {}


def work_notice(work_title, config=None):
    """작품명 → (work_display, notice_lines). 설정이 없으면 (None, []).

    권리사 가이드(laeebly licensed_video.guide)의 표기 요구를 **사람이 읽고** 이 설정에 옮겨 적는다."""
    cfg = config if config is not None else load_notice_config()
    rec = cfg.get(work_title) or {}
    lines = rec.get("notice_lines") or []
    if isinstance(lines, str):
        lines = [lines]
    return rec.get("work_display"), list(lines)


# 지오블락(대한민국 한정 노출) 필수 여부 — 권리사 가이드 문구로 판정.
GEOBLOCK_HINT_RE = re.compile(r"지오\s?블(락|럭)|geo\s?block", re.I)


def guide_requires_geoblock(guide_html):
    """권리사 가이드가 지오블락을 요구하는가. 순수. (laeebly 기준 21개 작품이 해당)"""
    if not guide_html:
        return False
    return bool(GEOBLOCK_HINT_RE.search(re.sub(r"<[^>]+>", " ", guide_html)))


def channel_geoblock_capable(channel):
    """채널이 지오블락 처리가 가능한가 — config/channels.json 의 geoblock_capable.
    미등록/미표기는 **불가로 본다**(안전측). 현재 가능한 채널은 재미쇼츠뿐."""
    rec = registry.resolve(channel) or {}
    return bool(rec.get("geoblock_capable"))


def geoblock_ok(work_guide, channel):
    """(통과여부, 사유). 지오블락 필수 작품을 처리 불가 채널로 올리면 차단한다.

    지오블락은 유튜브 PC 업로드에서 **사람이** 하는 설정이라 이 스크립트가 대신 해줄 수 없다.
    따라서 '가능한 채널에만 배정'이 유일한 방어선이고, 여기서 기계적으로 막는다.
    (근거: 유미의 세포들 가이드 "지오블락 불가한 채널은 참여 불가" — CLAUDE.md §3-1)"""
    if not guide_requires_geoblock(work_guide):
        return True, "지오블락 불필요"
    if channel_geoblock_capable(channel):
        return True, f"지오블락 필수 · {channel} 처리 가능"
    return False, (f"지오블락 필수 작품인데 '{channel}' 은 처리 불가 채널 "
                   f"(config/channels.json geoblock_capable) — 배정을 바꾸세요")


GRADE_SUFFIX = " (g)"          # laeebly 의 '(g)' 변형 작품 접미사
GRADE_COMPANY = "CJ ENM"       # 이 권리사면 (g) 코드를 써야 한다


def pick_licensed_row(rows, base_title):
    """licensed_video 행들 [(title, req_hashtags, code, company)] → 실제로 쓸 1건(없으면 None).

    laeebly 에는 같은 작품이 기본행과 '<작품명> (g)' 두 벌로 있는 경우가 있다(실측 7건, 전부
    CJ ENM). **권리사가 CJ ENM 이면 (g) 쪽 식별코드를 써야 한다**는 규칙이라 (g)를 우선한다.
    그 외에는 기본행. 순수 함수."""
    by = {r[0]: r for r in rows}
    base, gvar = by.get(base_title), by.get(base_title + GRADE_SUFFIX)
    if base is None and gvar is None:
        return None
    companies = {str(r[3] or "").strip().upper() for r in (base, gvar) if r is not None}
    if gvar is not None and GRADE_COMPANY.upper() in companies:
        return gvar
    return base or gvar


def fetch_licensed_row(work_title):
    """작품명 → 실제로 쓸 licensed_video 행 (title, req_hashtags, code, company, guide) 또는 None.

    ⚠️ 제목 **완전일치**만 사용한다(기본행 + '(g)' 변형 두 제목만 조회) — 부분일치로 고르면
    엉뚱한 작품의 권리코드를 붙이게 된다. CJ ENM 작품은 pick_licensed_row 가 (g)를 고른다.
    ⚠️ 그래서 작품명은 laeebly 표기와 정확히 같아야 한다(config/channels.json·DB works.title).
    laeebly 는 읽기전용 원천 DB(LAEEBLY_DB_URL)."""
    url = os.environ.get("LAEEBLY_DB_URL")
    if not url or not work_title:
        return None
    base = re.sub(r"\s*\(g\)$", "", work_title).strip()
    try:
        import psycopg
        with psycopg.connect(url) as conn, conn.cursor() as c:
            c.execute("""select title, required_hashtags_description, identification_code, company,
                                guide
                         from licensed_video where title in (%s, %s)""",
                      (base, base + GRADE_SUFFIX))
            rows = c.fetchall()
    except Exception as exc:  # 원천 DB 장애가 발행을 막지는 않게 — 경고만
        print(f"[주의] laeebly 조회 실패({type(exc).__name__}) — 식별코드·권리규칙 확인 불가")
        return None
    if len(rows) > 2:  # 동명 중복행 → 임의 선택 금지
        print(f"[주의] laeebly에 {base!r} 동명 행 {len(rows)}건 — 임의 선택하지 않음 "
              f"(--work-code 로 지정하세요)")
        return None
    row = pick_licensed_row(rows, base)
    if row is None:
        print(f"[주의] laeebly에서 {base!r} 완전일치 없음 — 작품명이 laeebly 표기와 다를 수 있습니다 "
              f"(--work-code 로 지정하세요)")
        return None
    if row[0] != base:
        print(f"[laeebly] {row[3]} 규칙 → '{row[0]}' 행 사용")
    return row


def hashtags_from_row(row):
    """licensed_video 행 → 설명란 해시태그 목록. 순수.
    우선순위: required_hashtags_description(라이선서 명시 원문) > '#'+identification_code."""
    if not row:
        return []
    _, req, code, _, _ = row
    if (req or "").strip():
        return parse_hashtags(req)
    return [code.strip()] if (code or "").strip() else []


def fetch_work_hashtags(work_title):
    """작품명 → laeebly가 요구하는 설명란 해시태그 목록. 없거나 조회 실패면 []."""
    return hashtags_from_row(fetch_licensed_row(work_title))


def fetch_episode(conn, clip_id):
    """clip → 실제 방영 회차(int) 또는 None.

    ⚠️ clips.episode 는 회차가 아니라 멱등 라벨('shorts_1'…)이라 쓸 수 없다. 실제 회차는
    gen_queue.episode 에만 있으므로 clip_metadata.ai_video_run_id ↔ gen_queue.run_id 로 잇는다.
    큐를 거치지 않은 수동 런(예: Drive 소스)은 행이 없어 None → 이때는 --episode 로 넘겨야 한다."""
    with conn.cursor() as c:
        c.execute("""select gq.episode from public.clip_metadata m
                     join public.gen_queue gq on gq.run_id = m.ai_video_run_id
                     where m.clip_id=%s and gq.episode is not null limit 1""", (clip_id,))
        r = c.fetchone()
    return r[0] if r else None


# ─────────────────────────── YouTube 업로드 ───────────────────────────

def _credentials(channel):
    """채널별 refresh token + (프로젝트 분리) 프로젝트별 OAuth 클라이언트로 Credentials 조립.
    OAuth 클라이언트는 gcp_project 별이고 **전역 키로 폴백하지 않는다**(2026-07-29). refresh token 은
    발급한 클라이언트에만 묶이므로, 짝이 아닌 클라이언트로는 어차피 갱신이 거부된다 — 폴백은 오타·
    미설정을 여기서 조용히 삼킨 뒤 밤중 업로드 단계에서 unauthorized_client 로 터뜨릴 뿐이었다.
    (실제로 gcp_project 가 폐기된 P* 를 가리키는 동안 폴백이 그 사실을 가리고 있었다.)
    refresh token 도 채널별만(generic 폴백 없음, §3-5) — token_env_name 이 미등록 채널을 하드
    실패시키므로 여기 도달하면 등록 채널이 보장됨."""
    from google.oauth2.credentials import Credentials
    rec = registry.resolve(channel)
    cid_key, cs_key = registry.client_env_names(rec.get("gcp_project") if rec else None)
    cid = os.environ.get(cid_key)
    cs = os.environ.get(cs_key)
    rt = os.environ.get(token_env_name(channel))   # §3-5: generic 폴백 없음(채널별 토큰만)
    if not (cid and cs and rt):
        return None
    # scopes 를 넘기지 않는다 — 넘기면 access token 이 그 부분집합으로 좁혀진다(위 주석 참고).
    return Credentials(None, refresh_token=rt, client_id=cid, client_secret=cs,
                       token_uri="https://oauth2.googleapis.com/token")


def upload(video_path, snippet, privacy, channel, publish_at=None):
    """YouTube videos.insert → content_id. OAuth 미설정이면 RuntimeError.
    publish_at(ISO 8601, tz 포함) 지정 시 유튜브 네이티브 예약 공개 — privacy 는 private 여야
    하며(YouTube 규칙) 그 시각에 유튜브가 알아서 public 전환한다. 토큰이 upload 스코프뿐이라
    사후 videos.update(공개 전환)가 403 나는 문제(2026-07-27 실측)를 업로드 시점 예약으로 우회."""
    creds = _credentials(channel)
    if creds is None:
        # 폴백을 없앴으므로 어느 키가 비었는지 이름으로 찍는다 — 채널마다 키 이름이 다르다.
        rec = registry.resolve(channel)
        cid_key, cs_key = registry.client_env_names(rec.get("gcp_project") if rec else None)
        missing = [k for k in (cid_key, cs_key, token_env_name(channel)) if not os.environ.get(k)]
        raise RuntimeError(f"YouTube OAuth 미설정 — .env 에 없음: {', '.join(missing)}")
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    yt = build("youtube", "v3", credentials=creds)
    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}
    if publish_at:
        status["privacyStatus"] = "private"   # publishAt 은 private 에서만 유효
        status["publishAt"] = publish_at
    body = {"snippet": snippet, "status": status}
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
    ap.add_argument("--episode", type=int, default=None,
                    help="방영 회차(설명란 '<작품명> N화'). 미지정 시 gen_queue에서 run_id로 자동 해석")
    ap.add_argument("--work-code", default=None,
                    help="작품 식별코드 해시태그(예: o483K). 미지정 시 laeebly licensed_video 에서 "
                         "작품명 완전일치로 자동 조회")
    ap.add_argument("--hashtags", nargs="*")
    ap.add_argument("--safety-floor", type=float, default=None,
                    help="명백히 깨진 산출물 차단용 안전 바닥. 미지정=judge quality로 안 막음(성과예측 아님)")
    ap.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    ap.add_argument("--publish-at", default=None,
                    help="유튜브 예약 공개 시각(ISO 8601, tz 포함). 지정 시 private 로 올라가 "
                         "그 시각에 자동 public 전환(YouTube 네이티브 예약)")
    ap.add_argument("--publish", action="store_true", help="실제 업로드(기본 dry-run)")
    a = ap.parse_args()

    import psycopg
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    try:
        db_title, work = fetch_clip_title(conn, a.clip_id)
        title = a.title or db_title
        hashtags = a.hashtags or ([work] if work else [])
        # 회차: --episode 명시값 우선, 없으면 gen_queue 에서 run_id 로 해석(큐 미경유 런은 None)
        episode = a.episode if a.episode is not None else fetch_episode(conn, a.clip_id)
        if episode is None:
            print("[주의] 회차 미상 — 설명란에 '<작품명> N화' 줄이 빠집니다. "
                  "큐를 거치지 않은 런이면 --episode <N> 으로 지정하세요.")
        # laeebly 권리 정보 1회 조회 — 식별코드 해시태그 · 필수 표기 판정 · 지오블락 게이트에 함께 씀
        lic_row = fetch_licensed_row(work)
        work_guide = lic_row[4] if lic_row else None
        work_tags = parse_hashtags(a.work_code) if a.work_code else hashtags_from_row(lic_row)
        if not work_tags:
            print("[주의] 작품 식별코드 해시태그 없음 — 라이선스 표기 요구가 있는 작품이면 "
                  "--work-code <코드> 로 지정해 다시 발행하세요.")
        # 권리사 필수 표기(설명란 문구) — config/work_publish_notice.json 에 사람이 옮겨 적은 값
        work_display, notice_lines = work_notice(work)
        if not (work_display or notice_lines) and guide_requires_notice(work_guide):
            print(f"[경고] laeebly 가이드가 설명란 표기를 요구하는 것으로 보이는데 "
                  f"config/work_publish_notice.json 에 {work!r} 설정이 없습니다. "
                  f"가이드를 확인하고 등록한 뒤 발행하세요.")
        snip = build_snippet(title, hashtags, work_title=work, episode=episode,
                             work_hashtags=work_tags, work_display=work_display,
                             notice_lines=notice_lines)
        # 지오블락 게이트 — 스크립트가 대신 못 하는 업로드 설정이라 배정 자체를 막는다(§3-1)
        geo_ok, geo_reason = geoblock_ok(work_guide, a.channel)
        print(f"geoblock: {'PASS' if geo_ok else 'BLOCK'} ({geo_reason})")
        ok, reason = gate_ok(conn, a.clip_id, a.safety_floor)
        print(f"gate: {'PASS' if ok else 'BLOCK'} ({reason}) | title={snip['title']!r} privacy={a.privacy} oauth={'O' if _credentials(a.channel) else 'X(미설정)'}")
        if not a.publish:
            print("[dry-run] --publish 시 실제 업로드. 안전: 게이트 통과 + opt-in 필요.")
            return
        if not geo_ok:
            sys.exit(f"지오블락 게이트 차단 — 발행 안 함: {geo_reason}")
        if not ok:
            sys.exit(f"게이트 차단 — 발행 안 함: {reason}")
        vid = upload(a.video, snip, a.privacy, a.channel, publish_at=a.publish_at)
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
