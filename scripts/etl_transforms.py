"""M0 ETL 순수 변환 규칙 (DB 의존 없음 → 단위 테스트 대상).

설계 근거: docs/M0_ETL.md, docs/SELF_IMPROVEMENT_SPEC.md.
여기엔 성과 윈도우 산식·Shorts 처리 등 '설계 결정'만 둔다. DB I/O는 etl_laeebly_to_pipeline.py.
"""
from __future__ import annotations

SHORTS_MAX_SEC = 180        # Shorts(<=3분); laeebly 데이터의 85%는 <=60s
DEFAULT_COVERAGE = 0.7      # +N일 윈도우에서 요구하는 최소 일자 커버리지 비율


def parse_len_sec(raw):
    """video_length(varchar 초) → float|None. 숫자 문자열만 허용(아니면 None)."""
    if raw is None:
        return None
    try:
        v = float(str(raw).strip())
    except ValueError:
        return None
    return v if v >= 0 else None


def is_short(len_sec):
    """Shorts 여부 (len_sec <= 180). 길이 미상이면 False(롱폼 취급 = 안전)."""
    return len_sec is not None and len_sec <= SHORTS_MAX_SEC


def avg_view_pct(watch_hours, views, len_sec):
    """APV% = watch_hours*3600 / views / len_sec. 분모 0/None이면 None."""
    if not views or not len_sec or watch_hours is None:
        return None
    return float(watch_hours) * 3600.0 / float(views) / float(len_sec)


def min_days_for_window(n, coverage=DEFAULT_COVERAGE):
    """+N일 윈도우에서 유효로 인정할 최소 커버 일자 수(부족하면 스킵)."""
    return max(1, int(coverage * n))


def perf_values(views, watch_hours, impressions, ctr, len_sec):
    """clip_performance 적재값 (views, avg_view_pct, impressions, ctr) 산출.
    Shorts는 ctr=None — swipe 기반이라 썸네일 CTR 무의미 (SPEC §0-1a)."""
    v = views or 0
    apv = avg_view_pct(watch_hours, v, len_sec)
    out_ctr = None if is_short(len_sec) else ctr
    return v, apv, impressions, out_ctr
