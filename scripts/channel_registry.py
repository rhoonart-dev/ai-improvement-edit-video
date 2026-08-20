#!/usr/bin/env python3
"""채널 레지스트리 — 발행 대상 채널의 토큰/OAuth 매핑 단일 소스(config/channels.json).

기존 CHANNEL_ENV 하드코딩(2채널 dict) + reconcile TARGET_CHANNELS + autoloop TARGETS 를 대체한다.
채널 추가 = JSON 한 항목 추가(코드/DB 변경 0). 매핑은 배포/자격증명 성격이라 분석 스키마(channels
테이블)와 분리해 파일로 관리한다. publish_youtube(발행)·get_youtube_token(토큰발급)·autoloop/
reconcile(라우팅)이 이 모듈을 공유 → 드리프트 제거. 외부 의존 없이 표준 라이브러리만 사용.

env 키 규약(값은 .env/시크릿에만; config엔 매핑만):
  refresh token   : YT_REFRESH_TOKEN_<token_slug>
  OAuth 클라이언트 : YT_CLIENT_ID_<gcp_project> / YT_CLIENT_SECRET_<gcp_project>
                    (gcp_project 가 DEFAULT/빈값 → 전역 YT_CLIENT_ID/YT_CLIENT_SECRET; 기존 2채널 무중단)

레코드 필드: token_slug(필수·env안전·유일), name(표시명), handle(@핸들|null),
             channel_id(UC…|null; 발급 시 자동확인·백필), gcp_project, genre, works[].
"""
from __future__ import annotations

import json
import os
import pathlib
import socket
import unicodedata

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "channels.json"
DEFAULT_WORKS_PATH = REPO_ROOT / "config" / "works.json"
DEFAULT_ASSIGNMENTS_PATH = REPO_ROOT / "config" / "assignments.json"
DEFAULT_POLICY_PATH = REPO_ROOT / "config" / "loop_policy.json"
DEFAULT_MACHINE_LOCAL_PATH = REPO_ROOT / "config" / "scene_loop.local.json"
DEFAULT_PROJECT = "DEFAULT"


# ─────────────────────────── 순수 헬퍼 (단위테스트) ───────────────────────────

def token_env_name(token_slug):
    """token_slug → refresh token env 변수명. 슬러그 없으면 generic 폴백(YT_REFRESH_TOKEN)."""
    return f"YT_REFRESH_TOKEN_{token_slug}" if token_slug else "YT_REFRESH_TOKEN"


def client_env_names(gcp_project):
    """gcp_project → (client_id 키, client_secret 키). DEFAULT/빈값이면 전역 키(기존 무중단)."""
    if not gcp_project or gcp_project == DEFAULT_PROJECT:
        return "YT_CLIENT_ID", "YT_CLIENT_SECRET"
    return f"YT_CLIENT_ID_{gcp_project}", f"YT_CLIENT_SECRET_{gcp_project}"


def _norm_name(s):
    """이름 비교용: 공백 제거 + 소문자('스토리 순삭'=='스토리순삭')."""
    return "".join((s or "").split()).lower()


def _norm_handle(s):
    """핸들 비교용: 앞의 @ 제거 + 소문자."""
    return (s or "").lstrip("@").strip().lower()


def norm_work_title(s, *, fold=False):
    """작품명 비교용 — NFC 정규화 + 공백 제거. ★시즌 표기·기호를 지우지 않는다.

    _norm_name(채널명용)과 별개로 두는 이유:
      ① factory/cluster._norm_title 은 '시즌\\d+' 를 지운다 → 'SNL 코리아 리부트 시즌7'(엔딩순삭)과
         '…시즌8'(킥킥극장·몰입도둑)이 같은 작품이 된다. 어느 라이선스 행을 쓸지 가르는 구분이라
         권리 경로엔 절대 부적합하다.
      ② macOS 는 한글을 NFD 로 주는 경우가 있어 눈으로 같은 문자열이 바이트로 다르다 — dict 조회와
         SQL 완전일치가 조용히 실패한다. NFC 로 접는다.
      ③ 이 함수는 **근접 오류 감지·후보 제시 전용**이다. 권리 레코드 선택은 끝까지 완전일치만
         (publish_youtube.fetch_licensed_row). 여기서 느슨하게 맞추면 엉뚱한 작품의 가이드를 적용한다.
    fold=True 는 검증 스크립트의 후보 제시에서만 쓴다(대소문자까지 접음).
    """
    t = unicodedata.normalize("NFC", s or "")
    t = "".join(t.split())
    return t.casefold() if fold else t


def same_work_title(a, b):
    """두 작품명이 '사실상 같은가'(공백·정규화 차이만) — 경고·후보 제시용. 순수."""
    return bool(a) and bool(b) and norm_work_title(a, fold=True) == norm_work_title(b, fold=True)


# ─────────────────────────── 로드 / 해석 ───────────────────────────

def config_path(path=None):
    return pathlib.Path(path) if path else pathlib.Path(os.environ.get("CHANNELS_CONFIG") or DEFAULT_CONFIG_PATH)


def load_channels(path=None):
    """config/channels.json 로드 → 레코드 리스트. 파일 없으면 []. (list 또는 {channels:[...]} 허용)"""
    p = config_path(path)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else (data.get("channels") or [])


def resolve(query=None, records=None, *, channel_id=None, handle=None, name=None):
    """채널 식별 → 레코드. 우선순위: channel_id(정확) > handle > name/query(공백무시). 실패 시 None.

    query: 발행 경로 편의 인자(채널 표시명 문자열). channel_id/handle/name: 토큰발급 경로용(API 결과).
    records 를 주면 파일을 읽지 않음(테스트/성능).
    """
    recs = records if records is not None else load_channels()
    cid = (channel_id or "").strip()
    if cid:
        for r in recs:
            if (r.get("channel_id") or "").strip() == cid:
                return r
    h = _norm_handle(handle)
    if h:
        for r in recs:
            if _norm_handle(r.get("handle")) == h:
                return r
    nm = _norm_name(name if name is not None else query)
    if nm:
        for r in recs:
            if _norm_name(r.get("name")) == nm:
                return r
            # 이름 질의를 핸들/슬러그로도 느슨히 허용(표시명 변형 대비)
            if _norm_handle(r.get("handle")) == nm or (r.get("token_slug") or "").lower() == nm:
                return r
    return None


def targets(records=None):
    """작품→채널명 라우팅 리스트[(work, channel_name)] — autoloop TARGETS 대체."""
    recs = records if records is not None else load_channels()
    return [(w, r.get("name")) for r in recs for w in (r.get("works") or []) if w and r.get("name")]


def channel_names(records=None):
    """등록된 채널 표시명 튜플 — reconcile TARGET_CHANNELS 대체."""
    recs = records if records is not None else load_channels()
    return tuple(r.get("name") for r in recs if r.get("name"))


# ─────────────────── 루프 운영 정본 (배정·작품 카드·정책) ───────────────────
#
# 왜 여기인가: channels.json 의 소유자가 이미 이 모듈이고, 조회 함수마다 records= 주입 seam 이 있어
# 파일 없이 테스트된다. 새 로더를 따로 만들면 '같은 정보를 읽는 로더'가 또 늘어난다.
#
# 정본 구분:
#   config/channels.json     채널 자격·능력 + 채널→작품 배정      (공유)
#   config/works.json        작품 카드 — 소스·회차 규칙 + 제약     (공유)
#   config/assignments.json  머신 → 담당 채널                      (공유)
#   config/loop_policy.json  전역 실행 정책                        (공유)
#   config/scene_loop.local.json  이 머신 값(machine·sources_root) (머신 전용)

def _load_json_config(path, env_key, default_path, *, missing_ok=True):
    p = pathlib.Path(path) if path else pathlib.Path(os.environ.get(env_key) or default_path)
    if not p.exists():
        if missing_ok:
            return {}
        raise FileNotFoundError(p)
    return json.loads(p.read_text(encoding="utf-8")) or {}


def load_works(path=None):
    """config/works.json → {정본제목: 카드}. 파일 없으면 {}."""
    return (_load_json_config(path, "WORKS_CONFIG", DEFAULT_WORKS_PATH).get("works") or {})


def load_assignments(path=None):
    """config/assignments.json → 전체 dict(machines 포함). 파일 없으면 {}."""
    return _load_json_config(path, "ASSIGNMENTS_CONFIG", DEFAULT_ASSIGNMENTS_PATH)


def load_loop_policy(path=None):
    """config/loop_policy.json → 전역 실행 정책 dict. 파일 없으면 {}."""
    return _load_json_config(path, "LOOP_POLICY_CONFIG", DEFAULT_POLICY_PATH)


def load_machine_local(path=None):
    """config/scene_loop.local.json → 이 머신 값. 없어도 되므로 {} 폴백."""
    return _load_json_config(path, "SCENE_LOOP_LOCAL_CONFIG", DEFAULT_MACHINE_LOCAL_PATH)


# ── 머신 식별 ──

def detect_machine_id(assignments=None, *, explicit=None, env=None, local=None,
                      hostname=None, user=None):
    """이 컴퓨터가 배정 정본의 어느 항목인지 결정. 실패하면 LookupError — 절대 추측하지 않는다.

    우선순위: explicit(--machine) > env(SCENE_LOOP_MACHINE) > local(scene_loop.local.json)
              > aliases 자동 감지(hostname 부분일치 · user 정확일치, **단일 매칭만 인정**)

    🛑 미해결·다중매칭에서 '전 채널'로 폴백하지 않는다. 모르는 채로 돌면 남의 채널까지 생성한다.
    """
    a = assignments if assignments is not None else load_assignments()
    machines = a.get("machines") or {}

    for src, val in (("--machine", explicit), ("SCENE_LOOP_MACHINE", env),
                     ("scene_loop.local.json", local)):
        if val:
            if val not in machines:
                raise LookupError(
                    f"머신 '{val}'({src})가 배정 정본에 없습니다. "
                    f"config/assignments.json 의 machines 키: {sorted(machines) or '(비어 있음)'}")
            return val

    host = (hostname if hostname is not None else socket.gethostname()).lower()
    who = user if user is not None else (os.environ.get("USER") or "")
    hits = []
    for mid, rec in machines.items():
        al = rec.get("aliases") or {}
        by_host = any(h and h.lower() in host for h in (al.get("hostname") or []))
        by_user = any(u and u == who for u in (al.get("user") or []))
        if by_host or by_user:
            hits.append(mid)
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise LookupError(
            f"이 컴퓨터가 머신 {sorted(hits)} 여러 항목에 매칭됩니다 — aliases 를 좁히거나 "
            f".env 에 SCENE_LOOP_MACHINE 을 지정하세요")
    raise LookupError(
        f"이 컴퓨터를 배정 정본에서 찾지 못했습니다 (hostname={host!r}, user={who!r}). "
        f"config/assignments.json 에 항목을 추가하거나 .env 에 SCENE_LOOP_MACHINE 을 지정하세요")


def machine_record(machine_id, assignments=None):
    a = assignments if assignments is not None else load_assignments()
    return (a.get("machines") or {}).get(machine_id)


def machine_channels(machine_id, assignments=None):
    """이 머신이 담당하는 채널 표시명 리스트. 항목이 없으면 LookupError."""
    rec = machine_record(machine_id, assignments)
    if rec is None:
        raise LookupError(f"머신 '{machine_id}' 가 배정 정본에 없습니다")
    return list(rec.get("channels") or [])


# ── 채널 ↔ 작품 (targets() 와 달리 레코드를 잃지 않는다) ──

def works_of(channel, records=None):
    """채널 표시명 → 그 채널이 맡은 작품 리스트. 채널을 못 찾으면 []."""
    rec = resolve(channel, records)
    return list((rec or {}).get("works") or [])


def channels_of_work(work, records=None):
    """작품 → 그 작품을 쓰는 채널 표시명 리스트(한 작품을 여러 채널이 쓸 수 있다)."""
    recs = records if records is not None else load_channels()
    return [r.get("name") for r in recs
            if r.get("name") and any(same_work_title(w, work) for w in (r.get("works") or []))]


def work_card(work, works=None):
    """작품 카드 조회 — **완전일치만**. 근접 오류는 None 을 돌려 work_card_candidates 로 알린다."""
    w = works if works is not None else load_works()
    return w.get(work)


def work_card_candidates(work, works=None):
    """완전일치 실패 시 제시할 후보 — 정규화(NFC·공백·대소문자)하면 같아지는 키들. 순수."""
    w = works if works is not None else load_works()
    return sorted(k for k in w if same_work_title(k, work))


# ── 유효 설정 (★ scene_loop 가 실제로 쓰는 채널 dict 를 만든다) ──

def _parse_box(box, work):
    """'395x280' → (395, 280). 순수.

    형식이 틀리면 즉시 멈춘다 — 조용히 무시하면 로고가 기본 크기로 나가고, 밤중 생성에서는
    아무도 그 사실을 모른 채 발행된다."""
    try:
        w, h = (int(v) for v in str(box).lower().split("x"))
        if w <= 0 or h <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError(f"작품 '{work}': 잘못된 로고 박스 형식 {box!r} — 'WxH' 여야 합니다(예: 395x280)")
    return w, h


# 채널 템플릿(channels.json 레코드의 "design" dict) → 생성 CLI 플래그.
# 키는 ai-video 의 --design-* 플래그와 1:1 이라 새 채널 템플릿은 JSON 한 블록으로 끝난다
# (2026-08-07 운영자 방침: 채널마다 다른 상단 제목 색·작품명 위치 등 템플릿을 둔다).
# 작품 단위 branding(로고)과 별개 층이다 — 템플릿은 채널 정체성, 로고는 작품 권리물.
CHANNEL_DESIGN_FLAGS = {
    "title_y": "--design-title-y",
    "video_y": "--design-video-y",         # 영상영역 상단 Y(미지정=세로 중앙) — 위로 올려 하단 밴드 확보
    "title_font": "--design-title-font",
    "title_size": "--design-title-size",
    "title_color": "--design-title-color",      # 제목 1번째 줄
    "title_color2": "--design-title-color2",    # 제목 2번째 줄
    "subtitle_font": "--design-subtitle-font",  # 자막·TTS 자막 공통(ai-video 6d0f433)
    "subtitle_size": "--design-subtitle-size",
    "subtitle_color": "--design-subtitle-color",
    "subtitle_y_margin": "--design-subtitle-y-margin",
    "subtitle_style": "--design-subtitle-style",
    "tts_color": "--design-tts-color",
    "tts_size": "--design-tts-size",
    "tts_y_margin": "--design-tts-y-margin",
    "work_title_y": "--design-work-title-y",    # 작품명(하단) Y
    "work_font_size": "--design-work-font-size",
    "work_color": "--design-work-color",        # 작품명 색
    "aspect_ratio": "--design-aspect-ratio",
    # 플랫폼 표기 — 영상영역 왼쪽 상단 로고/텍스트(ai-video 2026-08-19). 권리사 '영상 내
    # 플랫폼 노출' 요구(가왕쇼 티빙 등)용. 이미지는 이름만 적으면 ai-video assets/logos 에서
    # 찾는다(작품 로고와 같은 규약). 이미지·텍스트 둘 다 있으면 이미지가 우선.
    "platform_image": "--design-platform-image",
    "platform_text": "--design-platform-text",
    "platform_x": "--design-platform-x",        # 영상영역 왼쪽 상단 기준 오프셋
    "platform_y": "--design-platform-y",
    "platform_image_width": "--design-platform-image-width",
    "platform_image_height": "--design-platform-image-height",
    "platform_font_size": "--design-platform-font-size",
    "platform_color": "--design-platform-color",
    "platform_align": "--design-platform-align",   # left(기본)|right — 가로 앵커
}

# 값 없는 스위치형 키 — {템플릿 키: (플래그, 플래그를 붙일 값)}.
# face_tracking:false 면 --no-reframe(얼굴 추종 크롭 끄기, ai-video c184e63). 인물이 고정된
# 인터뷰 소재는 확대하면 원본에 박힌 자막이 잘려 끄는 편이 낫고, 크롭 생성을 건너뛰어 생성도 빨라진다.
CHANNEL_DESIGN_SWITCHES = {
    "face_tracking": ("--no-reframe", False),
    # 대사 자막 끔(8/20) — false 면 소스에 자막이 있어도 이 채널은 안 그린다.
    # 관제(aivideo.CHANNEL_DESIGN_SWITCHES) 1:1 미러. scene_loop 에는 편집실이 없어
    # 예외 가드는 관제 쪽(design_for_job)에만 있다.
    "subtitles": ("--no-subtitles", False),
}


def channel_design_flags(design, channel):
    """채널 'design' 템플릿 dict → CLI 플래그 리스트. 순수. '_' 시작 키(_note 등)는 무시.

    모르는 키는 즉시 ValueError — 조용히 무시하면 오타 난 템플릿이 기본값으로 밤새 발행되고
    아무도 모른다(로고 박스 _parse_box 와 같은 '생성 전에 크게 실패' 원칙. 러너의
    check_assignments 게이트가 생성 비용을 쓰기 전에 잡는다)."""
    flags = []
    for k, v in (design or {}).items():
        if k.startswith("_"):
            continue
        if k in CHANNEL_DESIGN_SWITCHES:
            flag, on_value = CHANNEL_DESIGN_SWITCHES[k]
            if v is on_value:
                flags.append(flag)
            continue
        flag = CHANNEL_DESIGN_FLAGS.get(k)
        if not flag:
            raise ValueError(f"채널 '{channel}': 알 수 없는 design 키 {k!r} — "
                             f"허용 키: {sorted(CHANNEL_DESIGN_FLAGS) + sorted(CHANNEL_DESIGN_SWITCHES)}")
        flags += [flag, str(v)]
    return flags


# 작품 카드 editorial 허용 키 — ai-video app/modules/editorial.py 계약의 미러(1:1 규율).
# avoid=장면 금지(태깅→하드 필터·문구 금지) · rules=구성 절대 규칙(조합·길이 제약,
# 예: 같은 곡 음악 1분 이내) · prefer=방향(랭킹 편향) · tone=title/TTS 문체.
EDITORIAL_KEYS = frozenset({"avoid", "rules", "prefer", "tone"})


def editorial_flags(card, work):
    """작품 카드 editorial → --editorial-json 플래그. 순수. '_' 시작 키(_note 등)는 문서용이라 뺀다.

    모르는 키는 즉시 ValueError — 오타(avoids)가 조용히 무시되면 권리 지침 없이 밤새
    생성된다(design 템플릿·로고 박스와 같은 '생성 전에 크게 실패' 원칙). 값 형식의 상세
    검증은 ai-video 쪽 parse_editorial 이 한 번 더 한다(그쪽이 계약 정본)."""
    ed = {k: v for k, v in ((card or {}).get("editorial") or {}).items()
          if not k.startswith("_")}
    if not ed:
        return []
    unknown = set(ed) - EDITORIAL_KEYS
    if unknown:
        raise ValueError(f"작품 '{work}': 알 수 없는 editorial 키 {sorted(unknown)} — "
                         f"허용 키: {sorted(EDITORIAL_KEYS)}")
    return ["--editorial-json", json.dumps(ed, ensure_ascii=False, sort_keys=True)]


def _card_to_channel_config(channel, work, card, policy, sources_root, multi_work=False,
                            channel_design=None):
    """작품 카드 + 정책 → scene_loop 가 아는 **예전 채널 dict 모양**.

    레거시 모양으로 내는 이유: channel_plan·discover_episodes_for·index_episodes·rendered_scenes·
    build_cmd 를 한 줄도 고치지 않고, 신규·레거시 두 모드가 하류 코드 경로를 공유하게 하려는 것이다
    (경로가 갈리면 동작도 갈린다).
    """
    src = card.get("source") or {}
    kind = src.get("type")
    con = card.get("constraints") or {}

    flags = list(policy.get("gen_flags_base") or [])
    if con.get("subtitles") == "none":
        flags.append("--no-subtitles")
    # 채널 템플릿 — 같은 채널의 모든 작품에 일괄 적용 (channels.json 레코드 "design")
    flags += channel_design_flags(channel_design, channel)

    # 로고 — 작품 카드에 branding.logo 가 있을 때만 붙인다(없으면 종전대로 작품명 텍스트).
    # 크기·정렬은 정책 전역값이 기본이고 작품이 예외를 덮는다: 로고 비율이 작품마다 달라 전역값이
    # 안 맞는 경우가 실제로 있다(10:1 초광폭은 박스에 넣으면 얇은 띠가 된다).
    brand = card.get("branding") or {}
    if brand.get("logo"):
        box = brand.get("box") or policy.get("logo_box")
        align = brand.get("align") or policy.get("logo_align")
        flags += ["--design-work-image", brand["logo"]]
        if box:
            w, h = _parse_box(box, work)
            flags += ["--design-work-image-width", str(w), "--design-work-image-height", str(h)]
        if align:
            flags += ["--design-work-align", align]

    # 편집 지침 — 작품 카드에 editorial 이 있을 때만 붙는다(없으면 프롬프트 종전과 동일).
    flags += editorial_flags(card, work)

    out = {
        "channel": channel,
        "work_title": work,
        # 순차 운영 — 아직 착수하지 않은 작품. 채널↔작품 매핑(권리 관계)은 그대로 두고 진행만 멈춘다.
        # 🛑 소스 조회 **전에** 걸러야 의미가 있다: 착수 전 작품이 채널 전체 소스면 매일 밤 그 채널을
        #    통째로 훑는다(tvN Joy 113,577건, 2026-07-30 실측) — 회차 0개를 얻자고 치르는 비용이다.
        "_paused": bool(card.get("paused")),
        # 진행 슬롯 — 상태 파일과 산출물 디렉토리를 가르는 이름. 한 채널이 작품을 둘 이상 맡으면
        # (재미쇼츠 = 유미의 세포들 시즌3 + 언더커버셰프) 둘 다 EP1 부터 시작하는데, 채널명만으로
        # 키를 잡으면 앞 작품의 EP1 장면이 뒤 작품 EP1 의 진행분으로 섞인다 — 중복 판정도 quota
        # 카운트도 어긋난다. 작품이 하나면 채널명 그대로라 기존 상태·산출물 경로가 유지된다.
        "slot": f"{channel}·{work}" if multi_work else channel,
        "start_episode": src.get("start_episode", 1),
        # 회차당 장면 수 작품별 예외 — 없으면 키 자체를 두지 않는다(scene_loop.quota_of 가 정책
        # 전역값으로 폴백). None 을 넣어두면 '지정했는데 비어 있음'과 구분이 안 된다.
        **({"quota_per_episode": int(card["quota_per_episode"])}
           if card.get("quota_per_episode") is not None else {}),
        "gen_flags": flags,
        "_source_kind": kind,
        "_geoblock_required": bool(con.get("geoblock_required")),
        "_subtitles": con.get("subtitles"),
        "_origin": f"assignments → channels.json:{channel} → works.json:{work}",
    }
    if kind in ("youtube_playlist", "youtube_channel"):
        out["source_type"] = "youtube"
        out["source_url"] = src.get("url")
        out["title_episode_regex"] = src.get("episode_regex")
        out["min_source_duration_sec"] = src.get("min_source_duration_sec", 0)
        # 회차 표기가 없는 자사 롱폼 채널용(커리어데이·B급 스튜디오) — 업로드 순서를 회차로 삼는다.
        out["episode_order"] = src.get("episode_order")
        # 권리 범위가 '이 채널 중 특정 코너 제외' 인 작품용(B급: 청문회 제외).
        out["title_exclude_regex"] = src.get("title_exclude_regex")
    elif kind == "local":
        out["source_type"] = "local"
        out["source_dir"] = str(pathlib.Path(sources_root) / (src.get("dir_slug") or ""))
        out["video_glob"] = src.get("file_glob")
        out["episode_regex"] = src.get("episode_regex")
    else:
        raise ValueError(f"작품 '{work}': 알 수 없는 source.type={kind!r} "
                         f"(youtube_playlist | youtube_channel | local)")
    return out


def default_sources_root():
    """로컬 소스 기본 위치. ~/Downloads 는 macOS TCC 가 막아 ffmpeg 가 실패하므로 쓰지 않는다."""
    return str(REPO_ROOT.parent / "sources")


def effective_channel_configs(machine_id=None, *, records=None, works=None,
                              assignments=None, policy=None, sources_root=None,
                              machine_local=None):
    """머신 → 담당 채널 → 채널의 작품 → 작품 카드 → scene_loop 채널 dict 리스트.

    카드가 없는 작품은 ValueError 로 즉시 알린다 — 그 채널만 조용히 빠지면 '어제는 돌았는데
    오늘은 안 도는' 상태가 되고 원인을 찾기 어렵다.
    """
    recs = records if records is not None else load_channels()
    wks = works if works is not None else load_works()
    asg = assignments if assignments is not None else load_assignments()
    pol = policy if policy is not None else load_loop_policy()
    loc = machine_local if machine_local is not None else load_machine_local()
    root = sources_root or loc.get("sources_root") or default_sources_root()

    mid = machine_id or detect_machine_id(asg, env=os.environ.get("SCENE_LOOP_MACHINE"),
                                          local=loc.get("machine"))
    out = []
    for ch in machine_channels(mid, asg):
        rec = resolve(ch, recs)
        if rec is None:
            raise ValueError(f"배정된 채널 '{ch}' 가 config/channels.json 에 없습니다")
        works_of_ch = rec.get("works") or []
        for work in works_of_ch:
            card = work_card(work, wks)
            if card is None:
                cands = work_card_candidates(work, wks)
                raise ValueError(
                    f"작품 '{work}'(채널 {ch}) 카드가 config/works.json 에 없습니다"
                    + (f" — 후보: {cands}" if cands else ""))
            out.append(_card_to_channel_config(ch, work, card, pol, root,
                                               multi_work=len(works_of_ch) > 1,
                                               channel_design=rec.get("design")))
    return out


def backfill_channel_id(token_slug, channel_id, path=None):
    """발급 시 자동확인된 channel_id 를 config에 백필(비어있을 때만). 변경했으면 True.

    이후 토큰 발급/재발급 시 channel_id 정확매칭이 되어 표시명 변경·유사이름 오매칭을 원천 차단한다.
    """
    if not (token_slug and channel_id):
        return False
    p = config_path(path)
    if not p.exists():
        return False
    recs = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(recs, list):
        return False
    changed = False
    for r in recs:
        if r.get("token_slug") == token_slug and not (r.get("channel_id") or "").strip():
            r["channel_id"] = channel_id
            changed = True
    if changed:
        p.write_text(json.dumps(recs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed
