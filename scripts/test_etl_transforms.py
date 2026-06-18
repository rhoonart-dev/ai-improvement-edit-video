"""M0 ETL 순수 변환 단위 테스트.

실행: python3 scripts/test_etl_transforms.py   (pytest 없이도 동작)
      또는 pytest scripts/test_etl_transforms.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from etl_transforms import (  # noqa: E402
    avg_view_pct,
    is_short,
    min_days_for_window,
    parse_len_sec,
    perf_values,
)


def test_parse_len_sec():
    assert parse_len_sec("4349.0") == 4349.0
    assert parse_len_sec("58") == 58.0
    assert parse_len_sec(None) is None
    assert parse_len_sec("") is None
    assert parse_len_sec("12:34") is None      # 비숫자 포맷
    assert parse_len_sec("-5") is None


def test_is_short():
    assert is_short(60) is True
    assert is_short(180) is True               # 경계 포함
    assert is_short(181) is False
    assert is_short(4349) is False
    assert is_short(None) is False             # 미상 → 롱폼 취급(안전)


def test_avg_view_pct():
    assert avg_view_pct(10, 100, 60) == 6.0    # 10*3600/100/60 (재시청 多)
    assert abs(avg_view_pct(1, 100, 120) - 0.3) < 1e-9
    assert avg_view_pct(5, 0, 60) is None      # views 0
    assert avg_view_pct(5, 100, None) is None  # 길이 미상
    assert avg_view_pct(None, 100, 60) is None


def test_min_days_for_window():
    assert min_days_for_window(14) == 9        # int(0.7*14)=9
    assert min_days_for_window(7) == 4         # int(4.9)=4
    assert min_days_for_window(1) == 1         # 최소 1
    assert min_days_for_window(14, coverage=1.0) == 14


def test_perf_values_short_nulls_ctr():
    v, apv, imp, ctr = perf_values(views=1000, watch_hours=5, impressions=2000, ctr=4.5, len_sec=58)
    assert v == 1000 and imp == 2000
    assert ctr is None                          # Shorts → CTR NULL (SPEC §0-1a)
    assert apv == 5 * 3600 / 1000 / 58


def test_perf_values_longform_keeps_ctr():
    v, apv, imp, ctr = perf_values(views=1000, watch_hours=50, impressions=8000, ctr=4.5, len_sec=4349)
    assert ctr == 4.5                           # 롱폼은 CTR 보존
    assert v == 1000


def test_perf_values_none_views():
    v, apv, imp, ctr = perf_values(views=None, watch_hours=5, impressions=None, ctr=3.0, len_sec=60)
    assert v == 0 and apv is None and ctr is None


def _run():
    tests = [g for n, g in sorted(globals().items()) if n.startswith("test_") and callable(g)]
    for f in tests:
        f()
        print(f"ok  {f.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    _run()
