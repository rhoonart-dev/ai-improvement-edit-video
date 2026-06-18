"""순수 피처 집계 — ai-video 분석 산출(장면 경계 / VAD 발화구간)에서 clip_features 값 계산.

DB·무거운 의존 없음 → 단위 테스트 대상. 설계: docs/M1_FEATURE_EXTRACTION.md.
"""
from __future__ import annotations

import re
from statistics import pvariance


def cut_metrics(scene_starts, duration_sec):
    """장면 시작 시각(초) 리스트 → 컷 구조 피처.

    scene_starts: 장면 시작 시각들(초). 0 미포함이면 0을 보강.
    반환: cut_count, avg_shot_len_sec, cut_rhythm_var, transition_count, hook_timing_sec.
    """
    if not duration_sec or duration_sec <= 0:
        return {"cut_count": None, "avg_shot_len_sec": None, "cut_rhythm_var": None,
                "transition_count": None, "hook_timing_sec": None}
    starts = sorted(s for s in scene_starts if 0 <= s <= duration_sec)
    if not starts or starts[0] > 0:
        starts = [0.0] + starts
    bounds = starts + [float(duration_sec)]
    shot_lens = [b - a for a, b in zip(bounds, bounds[1:]) if b > a]
    cut_count = max(len(shot_lens) - 1, 0)                      # 장면 전환(컷) 수 = 장면수 - 1
    avg = sum(shot_lens) / len(shot_lens) if shot_lens else None
    var = pvariance(shot_lens) if len(shot_lens) > 1 else 0.0   # 컷 리듬 분산
    hook = starts[1] if len(starts) > 1 else None              # 첫 컷 타이밍
    return {
        "cut_count": cut_count,
        "avg_shot_len_sec": avg,
        "cut_rhythm_var": var,
        "transition_count": cut_count,
        "hook_timing_sec": hook,
    }


def speech_metrics(speech_spans, duration_sec):
    """발화 구간 [(start,end)] → speech_ratio / silence_ratio (겹침 병합 후)."""
    if not duration_sec or duration_sec <= 0:
        return {"speech_ratio": None, "silence_ratio": None}
    merged = _merge(speech_spans)
    speech = sum(e - s for s, e in merged)
    ratio = min(speech / duration_sec, 1.0)
    return {"speech_ratio": ratio, "silence_ratio": max(1.0 - ratio, 0.0)}


def _merge(spans):
    """겹치는 구간 병합. (start,end) 순서 무관 입력 허용."""
    norm = sorted((min(a, b), max(a, b)) for a, b in spans)
    out = []
    for s, e in norm:
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def ocr_subtitle_density(per_frame_has_text):
    """샘플 프레임들의 자막 텍스트 유무(bool 리스트) → 자막 밀도(0~1). 빈 입력이면 None."""
    flags = list(per_frame_has_text)
    if not flags:
        return None
    return sum(1 for f in flags if f) / len(flags)


def ratios_from_silencedetect(ffmpeg_stderr, duration_sec):
    """ffmpeg silencedetect stderr → {silence_ratio, speech_ratio}.

    (ai-video extract_vad_segments 가 스텁이라 ffmpeg 로 직접 산출.)
    speech_ratio = 1 - silence_ratio = 비침묵 비율(BGM 등 비발화 오디오 포함 근사).
    """
    if not duration_sec or duration_sec <= 0:
        return {"speech_ratio": None, "silence_ratio": None}
    durs = [float(x) for x in re.findall(r"silence_duration:\s*([\d.]+)", ffmpeg_stderr or "")]
    sil = min(max(sum(durs) / duration_sec, 0.0), 1.0)
    return {"silence_ratio": round(sil, 6), "speech_ratio": round(1.0 - sil, 6)}
