#!/usr/bin/env python3
""".env 자동 로더 — 엔트리 스크립트가 repo 루트 .env를 os.environ에 채운다(이미 설정된 값은 보존).

scripts가 .env를 자동 로드하지 않아(외부 export/inline 의존) 생기던 'YT_* 미설정'·'PIPELINE_DB_URL
없음' 류 문제를 방지. python-dotenv 같은 외부 의존 없이 표준 라이브러리만 사용.
사용: 엔트리 스크립트 main() 첫 줄에서 `load_env()` 호출. inline(`KEY=.. python ..`)이 항상 우선(override=False).

값 뒤 인라인 주석(`KEY=/path  # 머신마다 다름`)은 잘라낸다. 예전엔 주석이 값에 그대로 붙어
`AI_VIDEO_ROOT` 가 `/…/ai-video   # 머신마다 다름` 이 되었고, 조립된 python 경로가 존재하지 않아
scene_loop 이 4채널 전부 FileNotFoundError 로 죽었다(2026-07-30). '#' 이 값 자체에 들어가는 시크릿을
깨지 않으려고 **공백 뒤의 '#'** 만 주석으로 본다.
"""
from __future__ import annotations

import os
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# 값 뒤 인라인 주석 — 공백이 앞에 붙은 '#' 부터 줄 끝까지. 공백을 요구하는 이유는 시크릿에 '#'이
# 그대로 들어가는 경우(DB 비밀번호 등)를 값의 일부로 남겨야 하기 때문이다.
_INLINE_COMMENT = re.compile(r"\s#")


def _clean_value(raw_value):
    """값에서 둘러싼 따옴표와 인라인 주석을 떼어낸다.

    인용된 값은 닫는 따옴표까지를 값으로 보고 그 뒤(주석 포함)를 버린다 — ' # ' 를 값에 꼭 넣어야
    하면 따옴표로 감싸는 것이 탈출구다.
    """
    v = raw_value.strip()
    if v[:1] in ('"', "'"):
        quote = v[0]
        end = v.find(quote, 1)
        if end != -1:
            return v[1:end]
        # 닫는 따옴표가 없다 → 예전 동작으로 폴백(아래 공통 처리)
    m = _INLINE_COMMENT.search(v)
    if m:
        v = v[: m.start()]
    return v.strip().strip('"').strip("'")


def load_env(path=None, override=False):
    """repo 루트(또는 path)의 .env를 읽어 os.environ에 주입. override=False면 기존 값 보존. 로드한 dict 반환."""
    p = pathlib.Path(path) if path else REPO_ROOT / ".env"
    if not p.exists():
        return {}
    loaded = {}
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = _clean_value(v)
        if not k:
            continue
        if override or k not in os.environ:
            os.environ[k] = v
        loaded[k] = v
    return loaded
