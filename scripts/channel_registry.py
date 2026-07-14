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

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "channels.json"
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
