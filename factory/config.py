#!/usr/bin/env python
"""쇼츠 분석 공장 — 설정.  ★factory 독립: 외부 폴더(t3_extract 등) 참조 없음.

env 로딩 순서: os.environ > factory/.env  (factory/.env 하나로 자족)
필수 키:
  LAEEBLY_DB_URL        laeebly Postgres 직결 DSN (읽기전용으로만 사용).
                        Supabase 대시보드 > Connect > Session pooler URI.
  PIPELINE_URL          video-improvement-pipeline 프로젝트 URL
  PIPELINE_SERVICE_KEY  동 프로젝트 service role key
선택 키:
  GEMINI_API_KEY / GEMINI_MODEL   VLM 트랙 (없으면 결정론만)
"""
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent          # factory/ (모든 경로의 기준)
DOWNLOADS = HERE / "downloads"

ENV_KEYS = ["LAEEBLY_DB_URL", "PIPELINE_URL", "PIPELINE_SERVICE_KEY",
            "GEMINI_API_KEY", "GEMINI_MODEL"]

# ── 게이트 (+7D 커버리지 검증, ⚠upload_at=데이터 날짜 기준) ──────────────
GATE_FIRST_DATA_TOL_DAYS = 1       # min(upload_at) <= publish + 1d  (좌측절단 배제)
PERF_WINDOW_DAYS = 7               # 성과 창: publish ~ publish+7d (Σdelta)
SHORTS_MAX_SEC = 180               # 쇼츠 판별 길이 상한 (YT 3분 쇼츠 스펙). 게이트·기저 공통.
                                   #   초과·null은 롱폼/중간으로 보고 뱅크·기저에서 제외.

# ── Storage ──────────────────────────────────────────────────────────
VIDEO_BUCKET = "laeebly-shorts-video"           # 기존 버킷, 평면 {shorts_id}.mp4
YTDLP_FORMAT = "b[height<=480]/worst"           # pilot/clip-sources-test와 동일 화질

# ── 정규화·판단 v0 (pipeline_three_frames_v0.1.md §2.2-2.3) ──────────
BASELINE_WINDOW_DAYS = 90          # 채널 기저: 발행 이전 trailing 윈도우(자신 제외)
BASELINE_MIN_N = 5                 # 기저 표본 최소 (미달 → lift=null, 폴백 없음 — §2.2)
POP_MIN_N = 10                     # 백분위 모집단 최소 (같은 원작 → cluster → global)
SCORE_WEIGHTS = {                  # 결측 시 가용 가중치로 재분배. 조회=상대(lift)+절대 균형.
    "lift": 0.25,                  # lift_views 백분위 — 채널 대비 배수(채널 파워 제거)
    "views_abs": 0.15,             # 절대 +7D 조회수 백분위 — 실제 도달 규모(대형 채널 회복)
    "retention": 0.4,              # kept_watching_rate 백분위 (웨이브2·희소)
    "engagement": 0.2,             # (likes+shares+comments)/views 백분위
}
LABEL_CUTS = (33.0, 66.0)          # good/mid/bad 고정 컷: <33 bad, 33~66 mid, ≥66 good.
                                   #   점수가 이미 '이전 대비 백분위 가중합'(0~100)이라 고정 임계.
SCORE_VERSION = "v0.3"             # as-of + 고정컷 + 절대조회 균형(lift .25 / 절대 .15)

# ── 에코챔버 차단 (§3-2) ──────────────────────────────────────────────
# 자사(ai-video 발행) 채널 식별자. repo 에 채널 ID(UC…)가 없어 laeebly channel_name 기준.
#   적재 시 eb_shorts_features.origin='ours' 세팅 → 인출·모집단·기저에서 하드 제외.
#   채널 ID 를 확보하면 OUR_CHANNEL_IDS 에 추가(이름 변경 대비 이중 식별).
OUR_CHANNEL_NAMES = ("재미쇼츠", "스토리순삭")
OUR_CHANNEL_IDS: tuple = ()          # 알려지면 ("UC…",) 로 채움
# 골든 홀드아웃 클러스터(주입 금지 — 뱅크 오염·mode collapse 카나리아, §3-2).
#   Phase 2(주입 v0) 시작 전 표본 충분한 클러스터 1~2개를 지정할 것.
#   ⚠ 지정은 뱅크 채점(perf_label) 후 designate_holdout.py 로 — 미채점 데이터로는 못 고름.
INJECTION_HOLDOUT_CLUSTERS: frozenset = frozenset()
# 홀드아웃 작품 세트(노브 판정에 절대 불사용 — 분기별 champion 무편향 측정, §3-7).
#   반복 사용 작품에의 과적합을 추적. 채점·라운드 이력 축적 후 designate_holdout.py 로 지정.
HOLDOUT_WORKS: frozenset = frozenset()

SCHEMA_VERSION = "0.4"
CODE_VERSION = "factory-v0.1"
VLM_SLEEP_SEC = 2                  # Gemini 호출 간 최소 간격
IDENTIFY_TEXT_CONF = 0.8           # 비라이선스 원작 식별: 설명란(텍스트)만으로 이 이상 확신하면
                                   #   영상 안 보고 확정(B방식). 미만이면 영상까지 봄.
SQL_ID_CHUNK = 500                 # laeebly IN-list 청크
REST_CHUNK = 200                   # PostgREST upsert 청크


def _read_env_file(path: Path) -> dict:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def load_settings() -> dict:
    merged = {}
    merged.update(_read_env_file(HERE / ".env"))    # factory/.env 하나로 자족
    for k in ENV_KEYS:
        if os.environ.get(k):
            merged[k] = os.environ[k]
    merged.setdefault("GEMINI_MODEL", "gemini-2.5-flash")
    return merged
