#!/usr/bin/env python3
""".env 자동 로더 — 엔트리 스크립트가 repo 루트 .env를 os.environ에 채운다(이미 설정된 값은 보존).

scripts가 .env를 자동 로드하지 않아(외부 export/inline 의존) 생기던 'YT_* 미설정'·'PIPELINE_DB_URL
없음' 류 문제를 방지. python-dotenv 같은 외부 의존 없이 표준 라이브러리만 사용.
사용: 엔트리 스크립트 main() 첫 줄에서 `load_env()` 호출. inline(`KEY=.. python ..`)이 항상 우선(override=False).
"""
from __future__ import annotations

import os
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# 값 뒤 인라인 주석(` # …`) 제거용. **공백이 앞선 # 만** 주석으로 본다 — DSN 비밀번호나 토큰에
# 들어간 '#'(앞에 공백 없음)까지 잘라내면 조용히 틀린 자격증명이 된다.
_INLINE_COMMENT = re.compile(r"\s+#.*$")


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
        v = v.strip()
        if not (v.startswith(('"', "'")) and v.endswith(('"', "'")) and len(v) > 1):
            v = _INLINE_COMMENT.sub("", v)   # 따옴표로 감싼 값은 그대로(주석 기호도 값의 일부)
        v = v.strip().strip('"').strip("'")
        if not k:
            continue
        if override or k not in os.environ:
            os.environ[k] = v
        loaded[k] = v
    return loaded
