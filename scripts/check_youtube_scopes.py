#!/usr/bin/env python3
"""채널별 YouTube refresh token 이 **실제로 받은 권한(scope)** 을 조회해 공개 전환 가능 여부를 판정한다.

왜 필요한가: refresh token 에 붙은 scope 는 **발급 시점 동의에 고정**된다. 코드에서 SCOPES 를
넓혀도 이미 발급된 토큰은 그대로여서, 넓힌 사실이 코드만 보면 맞는 것처럼 보인다. 실제로 2026-07-27
밤 공개 전환이 전 채널에서 403 insufficientPermissions 로 죽었는데, 원인은 토큰이 upload(+readonly)
로만 발급돼 videos.update 권한이 없었던 것이다 — 업로드(unlisted)까지는 되니 낮에는 정상처럼 보였다.
그래서 '어느 채널 토큰을 재발급해야 하는가'는 추측하지 말고 tokeninfo 로 직접 확인한다.

판정 기준: 공개 전환(videos.update) 은 `youtube` 또는 `youtube.force-ssl` 이 있어야 한다.
`youtube.upload` 는 insert 전용이고, `youtube.readonly` 는 읽기 전용이라 둘 다로는 안 된다.

실행:
  .venv/bin/python scripts/check_youtube_scopes.py              # 이 머신 담당 채널
  .venv/bin/python scripts/check_youtube_scopes.py --all        # 등록된 전 채널
  .venv/bin/python scripts/check_youtube_scopes.py --channel 재미쇼츠

네트워크로 access token 을 1회 갱신한다(읽기 전용 — 영상·채널을 건드리지 않는다).
비밀값(refresh token·access token)은 출력하지 않는다.
"""
from __future__ import annotations

import argparse
import os
import sys

try:
    from envload import load_env
except ImportError:  # 단독 import 컨텍스트
    def load_env(*a, **k):
        return {}

import channel_registry as registry

# videos.update(privacyStatus 변경) 에 필요한 scope — 하나라도 있으면 공개 전환 가능
PUBLISH_SCOPES = (
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
)
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"


# ─────────────────────────── 순수 (단위테스트) ───────────────────────────

def can_transition(granted):
    """granted scope 목록으로 공개 전환(videos.update) 가능 여부. 순수."""
    return any(s in set(granted or ()) for s in PUBLISH_SCOPES)


def missing_env_keys(channel, env=None):
    """그 채널 조회에 필요한데 env 에 없는 키 이름 목록. 순수(env 주입)."""
    env = os.environ if env is None else env
    rec = registry.resolve(channel)
    cid_key, cs_key = registry.client_env_names(rec.get("gcp_project") if rec else None)
    tok_key = registry.token_env_name(rec.get("token_slug")) if rec else None
    keys = [k for k in (cid_key, cs_key, tok_key) if k]
    return [k for k in keys if not env.get(k)]


def verdict_line(channel, granted, error=None):
    """사람이 읽는 한 줄. 순수 — 출력 형식을 테스트에서 고정할 수 있게 분리."""
    if error:
        return f"  ✗ {channel}: 조회 실패 — {error}"
    short = sorted(s.rsplit("/", 1)[-1] for s in granted)
    if can_transition(granted):
        return f"  ✓ {channel}: 공개 전환 가능 (scope: {', '.join(short)})"
    return (f"  ⛔ {channel}: 공개 전환 불가 — videos.update 권한 없음 "
            f"(scope: {', '.join(short) or '(없음)'}) → 재발급 필요")


# ─────────────────────────── 조회 (I/O) ───────────────────────────

def granted_scopes(channel):
    """그 채널 refresh token 의 실제 granted scope 집합. (scopes, error) 반환.

    refresh 응답의 `scope` 를 우선 쓰고(google-auth 가 granted_scopes 로 노출), 비어 있으면
    tokeninfo 로 확인한다. 구버전 google-auth 는 granted_scopes 가 없어 tokeninfo 경로를 탄다."""
    missing = missing_env_keys(channel)
    if missing:
        return [], f".env 에 없음: {', '.join(missing)}"
    rec = registry.resolve(channel)
    cid_key, cs_key = registry.client_env_names(rec.get("gcp_project"))
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    creds = Credentials(
        None,
        refresh_token=os.environ[registry.token_env_name(rec["token_slug"])],
        client_id=os.environ[cid_key],
        client_secret=os.environ[cs_key],
        token_uri="https://oauth2.googleapis.com/token",
    )
    try:
        creds.refresh(Request())
    except Exception as e:  # noqa: BLE001 — 갱신 거부(unauthorized_client 등)도 진단 결과다
        return [], f"{type(e).__name__}: {e}"
    scopes = list(getattr(creds, "granted_scopes", None) or [])
    if scopes:
        return scopes, None
    try:
        import urllib.parse
        import urllib.request
        url = f"{TOKENINFO_URL}?{urllib.parse.urlencode({'access_token': creds.token})}"
        with urllib.request.urlopen(url, timeout=20) as r:  # noqa: S310 — 상수 호스트
            import json
            info = json.loads(r.read().decode("utf-8"))
        return (info.get("scope") or "").split(), None
    except Exception as e:  # noqa: BLE001
        return [], f"tokeninfo {type(e).__name__}: {e}"


def main():
    load_env()
    ap = argparse.ArgumentParser(description="채널별 YouTube 토큰 scope 점검(공개 전환 가능 여부)")
    ap.add_argument("--channel", action="append", help="점검할 채널(여러 번 지정 가능)")
    ap.add_argument("--all", action="store_true", help="config/channels.json 등록 전 채널")
    ap.add_argument("--machine", help="배정 정본의 머신 id(자동 감지 대신 명시)")
    a = ap.parse_args()

    if a.channel:
        channels = a.channel
        scope_label = "지정"
    elif a.all:
        channels = list(registry.channel_names())
        scope_label = "전 채널"
    else:
        machine_id = registry.detect_machine_id(explicit=a.machine)
        channels = registry.machine_channels(machine_id)
        scope_label = f"{machine_id} 담당"

    print(f"=== YouTube 토큰 scope 점검 === (범위: {scope_label} · 채널 {len(channels)}개)")
    print("판정: 공개 전환(videos.update)에는 youtube 또는 youtube.force-ssl 이 필요하다\n")
    bad, err = [], []
    for ch in channels:
        scopes, error = granted_scopes(ch)
        print(verdict_line(ch, scopes, error))
        if error:
            err.append(ch)
        elif not can_transition(scopes):
            bad.append(ch)

    print(f"\n⛔ 공개 전환 불가 {len(bad)}건 · ✗ 조회 실패 {len(err)}건 · 총 {len(channels)}건")
    if bad:
        print("\n재발급 절차(채널마다 1회, 브라우저 로그인이 필요해 사람이 해야 한다):")
        for ch in bad:
            print(f"  .venv/bin/python scripts/get_youtube_token.py --client-secret <그 채널 gcp_project "
                  f"클라이언트.json> --channel '{ch}' --write-env")
        print("  → 브라우저에서 그 브랜드 채널을 선택하고 동의화면의 권한을 모두 켠 채 동의할 것.")
        print("  → 재발급 후 이 스크립트를 다시 돌려 ✓ 로 바뀌는지 확인.")
    return 1 if (bad or err) else 0


if __name__ == "__main__":
    sys.exit(main())
