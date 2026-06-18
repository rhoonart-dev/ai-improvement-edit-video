"""순수 피처 집계 단위 테스트. 실행: python3 extract/test_feature_aggregates.py (pytest도 가능)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from feature_aggregates import (  # noqa: E402
    cut_metrics,
    ocr_subtitle_density,
    ratios_from_silencedetect,
    speech_metrics,
    _merge,
)


def test_cut_metrics_even():
    m = cut_metrics([0, 10, 20, 30], 40)
    assert m["cut_count"] == 3            # 4 scenes → 3 cuts
    assert m["avg_shot_len_sec"] == 10
    assert m["cut_rhythm_var"] == 0.0     # 균일
    assert m["hook_timing_sec"] == 10
    assert m["transition_count"] == 3


def test_cut_metrics_prepends_zero():
    m = cut_metrics([5, 15], 30)          # 0 미포함 → 보강
    assert m["cut_count"] == 2            # scenes [0-5],[5-15],[15-30]
    assert m["avg_shot_len_sec"] == 10    # (5+10+15)/3
    assert abs(m["cut_rhythm_var"] - 16.6667) < 1e-3
    assert m["hook_timing_sec"] == 5


def test_cut_metrics_single_scene():
    m = cut_metrics([], 40)
    assert m["cut_count"] == 0
    assert m["avg_shot_len_sec"] == 40
    assert m["cut_rhythm_var"] == 0.0
    assert m["hook_timing_sec"] is None


def test_cut_metrics_zero_duration():
    m = cut_metrics([0, 5], 0)
    assert m["cut_count"] is None and m["hook_timing_sec"] is None


def test_speech_metrics_basic():
    m = speech_metrics([(0, 10), (20, 30)], 40)
    assert m["speech_ratio"] == 0.5
    assert m["silence_ratio"] == 0.5


def test_speech_metrics_overlap_merged():
    m = speech_metrics([(0, 10), (5, 15)], 30)   # 병합 → (0,15)
    assert abs(m["speech_ratio"] - 0.5) < 1e-9
    assert abs(m["silence_ratio"] - 0.5) < 1e-9


def test_speech_metrics_caps_at_one():
    m = speech_metrics([(0, 50)], 40)            # 발화 > 길이 → 1.0 캡
    assert m["speech_ratio"] == 1.0
    assert m["silence_ratio"] == 0.0


def test_speech_metrics_zero_duration():
    m = speech_metrics([(0, 5)], 0)
    assert m["speech_ratio"] is None and m["silence_ratio"] is None


def test_merge_unordered():
    assert _merge([(20, 30), (0, 10), (8, 12)]) == [(0, 12), (20, 30)]


def test_ocr_subtitle_density():
    assert ocr_subtitle_density([True, True, False, False]) == 0.5
    assert ocr_subtitle_density([True, True, True]) == 1.0
    assert ocr_subtitle_density([False, False]) == 0.0
    assert ocr_subtitle_density([]) is None


def test_ratios_from_silencedetect():
    s = "silence_start: 1\nsilence_end: 3 | silence_duration: 2.0\nsilence_duration: 3.0\n"
    r = ratios_from_silencedetect(s, 10)
    assert r["silence_ratio"] == 0.5 and r["speech_ratio"] == 0.5
    assert ratios_from_silencedetect("", 10) == {"silence_ratio": 0.0, "speech_ratio": 1.0}
    assert ratios_from_silencedetect("silence_duration: 99", 10)["silence_ratio"] == 1.0   # cap
    assert ratios_from_silencedetect("x", 0)["speech_ratio"] is None


def _run():
    tests = [g for n, g in sorted(globals().items()) if n.startswith("test_") and callable(g)]
    for f in tests:
        f()
        print(f"ok  {f.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    _run()
