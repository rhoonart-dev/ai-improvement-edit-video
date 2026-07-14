#!/usr/bin/env python3
"""YouTube 업로드용 OAuth refresh token 발급 + .env 자동 기록 (발행할 채널마다 1회).

google-auth-oauthlib InstalledAppFlow로 로컬 브라우저 로그인 → 동의 → refresh_token 발급.
발급 직후 channels.list(mine=true)로 **이 토큰이 실제 제어하는 채널**을 확인하고, 그 채널에 맞는
env 키 이름(YT_REFRESH_TOKEN_STORYSUNSAK / _JAEMISHOTS)으로 `.env`에 직접 기록한다(--write-env).

사전조건: Google Cloud Console에서 OAuth 클라이언트(유형=데스크톱 앱) 생성 → client_secret JSON 다운로드.
실행:
  <venv>/python scripts/get_youtube_token.py --client-secret /경로/client_secret.json --write-env
  → 브라우저에서 **발행할 채널(브랜드)** 선택·동의 → .env에 YT_CLIENT_ID/SECRET + 해당 채널 토큰 기록
  → 채널이 2개면 2번 실행하고, 각 실행에서 해당 브랜드 채널을 선택(자동 확인되어 올바른 키로 저장됨)

비밀값(refresh token)은 --write-env 시 화면에 출력하지 않고 gitignore된 .env에만 기록한다.
⚠️ OAuth 동의화면이 'testing'이면 refresh token이 7일 후 만료 → 안정 운용은 'In production'으로 게시.
"""
from __future__ import annotations

import argparse
import pathlib
import re

# upload(발행) + readonly(발급 직후 채널 자동확인용)
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

# 채널→토큰/OAuth 매핑 단일 소스(config/channels.json) — 하드코딩/퍼지매칭 제거
import channel_registry as registry


# ─────────────────────────── 순수 (단위테스트) ───────────────────────────

def resolve_record(title, channel_id, handle, intended, records, force=False):
    """OAuth로 잡힌 채널(title/channel_id/handle)과 지정(intended)으로 저장할 config 레코드를 결정. 순수.

    반환 (record|None, ok, message). ok=False면 저장 보류(미스매치/미확인/미등록). force=True면 경고만 하고 강제.
    channel_id 정확매칭을 우선 신뢰하므로, 'Laeebly(개인) 토큰을 브랜드 채널 키로 저장' 같은 조용한 오연결을 막는다.
    """
    detected = registry.resolve(records=records, channel_id=channel_id, handle=handle, name=title)
    has_signal = bool(title or channel_id or handle)
    if intended:
        want = registry.resolve(records=records, name=intended)
        if want is None:
            return None, force, f"지정 채널 '{intended}'이 config/channels.json에 없음 — 먼저 등록하세요"
        if detected and detected.get("token_slug") != want.get("token_slug"):
            return want, force, f"선택 채널 '{title or handle or channel_id}'(={detected.get('name')}) ≠ 지정 '{intended}' — 브라우저에서 잘못 선택했을 수 있음"
        if not has_signal:
            return want, True, "채널 자동확인 불가(readonly 권한 등) — 지정값을 신뢰. 업로드 후 채널 재확인 권장"
        if detected is None:
            return want, force, f"선택 채널 '{title}'이 '{intended}' 등록정보와 매칭 안 됨 — 같은 채널이면 --force"
        return want, True, ""
    # intended 미지정 → 자동확인된 등록 채널로만 저장(개인/미등록 채널이면 보류)
    if detected:
        return detected, True, ""
    if not has_signal:
        return None, False, "채널 자동확인 불가 & --channel 미지정 — 저장 보류"
    return None, False, f"채널 '{title}'이 등록 채널과 매칭 안 됨 — 발행 대상이면 config 등록 후 --channel 지정"


def upsert_env_text(text, updates):
    """기존 .env 텍스트에 key=value를 upsert(있으면 첫 줄 교체+중복 제거, 없으면 끝에 추가). 순수."""
    keys = set(updates)
    seen = set()
    out = []
    for raw in text.splitlines():
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", raw)
        if m and m.group(1) in keys:
            k = m.group(1)
            if k not in seen:
                out.append(f"{k}={updates[k]}")
                seen.add(k)
            continue  # 중복 라인 제거
        out.append(raw)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
            seen.add(k)
    return "\n".join(out) + "\n" if out else ""


# ─────────────────────────── OAuth / 채널 확인 (I/O) ───────────────────────────

def detect_channel(creds):
    """channels.list(mine=true) → (title, channel_id, handle). 실패 시 (None, None, None)."""
    try:
        from googleapiclient.discovery import build
        yt = build("youtube", "v3", credentials=creds)
        items = (yt.channels().list(part="snippet", mine=True).execute() or {}).get("items") or []
        if items:
            sn = items[0].get("snippet") or {}
            return sn.get("title"), items[0].get("id"), sn.get("customUrl")
    except Exception as e:  # noqa: BLE001 — 확인 실패해도 발급 자체는 진행
        print(f"  (채널 자동확인 실패: {type(e).__name__} {e})")
    return None, None, None


def main():
    ap = argparse.ArgumentParser(description="YouTube refresh token 발급 + .env 기록")
    ap.add_argument("--client-secret", required=True, help="Google Cloud OAuth 클라이언트 JSON 경로")
    ap.add_argument("--port", type=int, default=0, help="로컬 콜백 포트(기본 0=자동)")
    ap.add_argument("--channel", help="채널명 강제 지정(자동확인 실패/불일치 시). 예: 스토리순삭")
    ap.add_argument("--write-env", action="store_true", help=".env에 직접 기록(권장; 미지정 시 화면 출력만)")
    ap.add_argument("--force", action="store_true", help="채널 미스매치/미확인이어도 강제 저장")
    ap.add_argument("--env-path", default=str(pathlib.Path(__file__).resolve().parent.parent / ".env"),
                    help="기록 대상 .env 경로(기본: repo 루트 .env)")
    a = ap.parse_args()

    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(a.client_secret, SCOPES)
    # access_type=offline + prompt=consent → refresh_token 보장
    creds = flow.run_local_server(port=a.port, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        print("⚠️ refresh_token이 없습니다. prompt=consent로 재시도하거나 기존 동의를 해제 후 다시 실행하세요.")
        return

    title, ch_id, handle = detect_channel(creds)
    records = registry.load_channels()
    rec, ok, msg = resolve_record(title, ch_id, handle, a.channel, records, a.force)
    print()
    print(f"이 토큰이 제어하는 채널: {title or '(자동확인 실패)'}"
          + (f"  [{ch_id}]" if ch_id else "") + (f"  {handle}" if handle else ""))
    if msg:
        print(f"⚠️ {msg}")
    if not ok:
        print("→ 저장 보류. 브라우저에서 **발행할 브랜드 채널**을 선택해 재실행하세요(config/channels.json 등록분).")
        print("  (그 채널이 선택지에 안 보이면 = 브랜드 계정이 아니라 Studio 권한 위임 → 그 계정 토큰으론 API 발행 불가)")
        return

    slug = rec.get("token_slug")
    token_key = registry.token_env_name(slug)
    cid_key, cs_key = registry.client_env_names(rec.get("gcp_project"))  # 프로젝트 분리: 전용 클라이언트 키
    print(f"→ 채널={rec.get('name')} slug={slug} project={rec.get('gcp_project')}")
    print(f"→ 저장 키: {cid_key}, {cs_key}, {token_key}")

    updates = {
        cid_key: creds.client_id,
        cs_key: creds.client_secret,
        token_key: creds.refresh_token,
    }
    if a.write_env:
        p = pathlib.Path(a.env_path)
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        p.write_text(upsert_env_text(text, updates), encoding="utf-8")
        print(f"\n✅ .env 기록 완료: {p}")
        print(f"   기록한 키: {cid_key}, {cs_key}, {token_key}  (비밀값은 출력하지 않음)")
        # 자동확인된 channel_id 를 config에 백필(비어있을 때만) → 다음 발급부터 정확매칭
        if ch_id and registry.backfill_channel_id(slug, ch_id):
            print(f"   config/channels.json 백필: {slug}.channel_id={ch_id}")
        print("   채널이 더 있으면 이 명령을 다시 실행하고 브라우저에서 그 채널을 선택하세요.")
    else:
        print("\n========== 아래를 .env 에 복사 (또는 --write-env 로 자동 기록) ==========")
        for k, v in updates.items():
            print(f"{k}={v}")
        print("=====================================================================")


if __name__ == "__main__":
    main()
