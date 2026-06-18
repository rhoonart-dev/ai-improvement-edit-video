#!/usr/bin/env python3
"""
M1 x 추출 어댑터 — 기존(laeebly) 영상 파일 → clip_features (ai-video 분석 모듈 재사용).

설계: docs/M1_FEATURE_EXTRACTION.md. 순수 집계는 feature_aggregates.py(단위 테스트됨).
이 파일은 글루(ai-video import + ffmpeg/whisper/opencv/Gemini + DB upsert) — 통합 테스트 영역.
무거운 의존은 함수 내부에서 lazy import. ai-video .venv 로 실행할 것:
  GEMINI_API_KEY=... /Users/gimsewon/rhoonart/ai-video/.venv/bin/python extract/feature_extractor.py <video> --semantic --ocr

환경: AI_VIDEO_ROOT, GEMINI_API_KEY(scene-obs), PIPELINE_DB_URL(upsert).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from feature_aggregates import cut_metrics, ocr_subtitle_density, ratios_from_silencedetect

AI_VIDEO_ROOT = Path(os.environ.get("AI_VIDEO_ROOT", "/Users/gimsewon/rhoonart/ai-video"))
sys.path.insert(0, str(AI_VIDEO_ROOT))

# Gemini 2.5 Pro scene-observation 프롬프트 (SPEC §2-C). 관찰 전용·decouple·타임코드 근거.
SCENE_OBS_PROMPT = """너는 깐깐한 숏폼(쇼츠) 편집 심사자다. 아래 영상을 보고 '편집이 만든 구조/품질'만 관찰해 JSON으로만 답하라.
원작 IP의 유명세·내재적 드라마가 아니라 이 클립 자체의 편집 기여만 평가하라(decouple).
각 판단에 근거 타임코드 t(초)를 포함하고, 직접 확인 안 된 시점은 추측하지 마라.

[채점 원칙 — 매우 중요, 반드시 준수]
- 대부분의 쇼츠는 평범하다. 점수 중앙값이 0.4~0.5가 되도록 냉정하게 변별하라.
- 0~1 전 구간을 써라. 0.85+ 는 '이 한 편만으로 채널을 키울 드문 수작'에만 허용(전체의 ~10%).
- 모든 차원에 비슷한 점수를 남발하면 무효. 강점/약점을 점수로 분명히 갈라라.

[차원 앵커]
narrative_completeness: 0.2=맥락 없으면 이해 불가 / 0.5=대충 이해 / 0.8=앞뒤 없이 완결 / 1.0=완벽한 미니스토리
hook_semantic_strength: 0.2=밋밋 / 0.5=평범 / 0.8=즉시 궁금증·충격 / 1.0=스크롤 절대정지
climax_included: 명확한 감정/서사 절정이 실제로 담겼는가(애매하면 false)
dialogue_density / action_density: 0~1, 실제 밀도대로

반드시 아래 JSON 스키마로만 출력(설명 금지):
{
  "narrative_completeness": 0.0,
  "climax_included": false,
  "hook_semantic_strength": 0.0,
  "hook_type": "question|shock|preview|emotion|none",
  "dialogue_density": 0.0,
  "action_density": 0.0,
  "emotion_arc": [{"t": 0, "valence": 0.0, "arousal": 0.0}],
  "scene_sequence": [{"t0": 0, "t1": 0, "desc": "..."}],
  "main_characters": ["..."]
}"""

# 관찰형(사실) 추출 — 판단/점수 금지, 보이는 사실만. raw_features(jsonb)에 저장. (노트 "관찰 > 판단")
OBS_PROMPT = """너는 영상 분석가다. 아래 쇼츠를 보고 '사실만' 관찰해 JSON으로 답하라.
'좋다/나쁘다/강하다' 같은 판단·점수는 절대 쓰지 마라 — 보이는 사실만. 확인 안 되면 null.
각 카테고리(scene_type·emotion_category·motion_level·place_setting)는 보기 중 정확히 하나만 고른다.
JSON only:
{
  "scene_type": "대화|액션|리액션|정보전달|몽타주|기타",
  "num_speakers": 0,
  "conflict_present": false,
  "has_punchline": false,
  "emotion_category": "분노|감동|유머|긴장|놀람|슬픔|평온|기타",
  "motion_level": "낮음|보통|높음",
  "place_setting": "실내-가정|실내-스튜디오|야외|기타",
  "face_count_first3s": 0,
  "topic_shift_count": 0,
  "question_hook": false
}"""


def extract_deterministic(video_path: Path) -> dict:
    """B/D 결정론적 피처: media_probe + scene_detect + speech(VAD)."""
    import subprocess

    from app.modules import media_probe, scene_detect  # ai-video

    info = media_probe.probe_media(video_path)
    dur = info.duration_sec
    feats: dict = {"duration_sec": dur}
    scenes = scene_detect.detect_scenes(video_path, info.fps, dur)
    feats.update(cut_metrics([s.start_sec for s in scenes], dur))
    if info.has_audio:
        # ai-video extract_vad_segments 는 스텁(return []) → ffmpeg silencedetect 로 직접 산출.
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(video_path),
             "-af", "silencedetect=noise=-30dB:d=0.5", "-f", "null", "-"],
            capture_output=True, text=True,
        )
        feats.update(ratios_from_silencedetect(proc.stderr, dur))
    return feats


def extract_burned_subtitles(video_path: Path, sample_interval_sec: float = 1.0) -> dict:
    """번인 자막 OCR → subtitle_density(0~1). 하단 40% 영역만 검사. (신규 컴포넌트, SPEC §2-B/0-9)"""
    import cv2  # opencv (ai-video .venv)

    reader = _ocr_reader()
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(int(fps * sample_interval_sec), 1)
    flags, i = [], 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % step == 0:
            h = frame.shape[0]
            crop = frame[int(h * 0.6):, :]            # 자막은 보통 하단
            texts = [t for t in reader.readtext(crop, detail=0) if t and t.strip()]
            flags.append(len(texts) > 0)
        i += 1
    cap.release()
    return {"subtitle_density": ocr_subtitle_density(flags)}


def _ocr_reader():
    try:
        import easyocr
    except ImportError as e:
        raise RuntimeError("OCR 백엔드 없음: ai-video .venv 에 'pip install easyocr' 필요") from e
    return easyocr.Reader(["ko", "en"], gpu=False)


def extract_semantic(video_path: Path) -> dict:
    """C 의미 피처: Gemini 2.5 Pro scene-observation (단일 chunk, <=60s). GEMINI_API_KEY 필요."""
    import json
    import time

    from google import genai

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options={"timeout": 120000})
    f = client.files.upload(file=str(video_path))
    for _ in range(60):                                # 업로드 처리 대기(ACTIVE)
        f = client.files.get(name=f.name)
        state = getattr(f.state, "name", str(f.state))
        if state == "ACTIVE":
            break
        if state == "FAILED":
            raise RuntimeError("Gemini 파일 처리 실패")
        time.sleep(2)
    resp = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=[f, SCENE_OBS_PROMPT],
        config={"response_mime_type": "application/json", "temperature": 0.2},
    )
    d = json.loads(resp.text)
    return {
        "narrative_completeness": d.get("narrative_completeness"),
        "climax_included": d.get("climax_included"),
        "hook_semantic_strength": d.get("hook_semantic_strength"),
        "dialogue_density": d.get("dialogue_density"),
        "action_density": d.get("action_density"),
        "emotion_arc": d.get("emotion_arc"),           # jsonb (list)
        "scene_sequence": d.get("scene_sequence"),     # jsonb (list)
        "main_characters": d.get("main_characters"),   # jsonb (list)
        "raw_features": {"hook_type": d.get("hook_type")},
    }


def extract_observation(video_path: Path) -> dict:
    """관찰형 의미피처(사실) → raw_features(jsonb). 판단 아닌 사실만. GEMINI_API_KEY 필요."""
    import json
    import time

    from google import genai

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options={"timeout": 120000})
    f = client.files.upload(file=str(video_path))
    for _ in range(60):
        f = client.files.get(name=f.name)
        st = getattr(f.state, "name", str(f.state))
        if st == "ACTIVE":
            break
        if st == "FAILED":
            raise RuntimeError("Gemini 파일 처리 실패")
        time.sleep(2)
    resp = client.models.generate_content(
        model="gemini-2.5-pro", contents=[f, OBS_PROMPT],
        config={"response_mime_type": "application/json", "temperature": 0.2})
    return {"raw_features": json.loads(resp.text)}


def extract_features(video_path, meta=None, with_semantic=False, with_ocr=False, with_obs=False) -> dict:
    """clip_features 컬럼에 1:1 매핑되는 dict 반환."""
    video_path = Path(video_path)
    feats = extract_deterministic(video_path)
    if with_ocr:
        feats.update(extract_burned_subtitles(video_path))
    if with_semantic:
        feats.update(extract_semantic(video_path))
    if with_obs:
        feats.update(extract_observation(video_path))   # 관찰형 사실 → raw_features(jsonb)
    if meta and meta.get("title"):
        feats["title_text"] = meta["title"]            # E 패키징(임베딩은 별도 단계)
    return feats


def upsert_features(conn, clip_id, feature_version, feats: dict) -> None:
    """clip_features upsert. conn = psycopg connection(PIPELINE_DB_URL). dict/list → jsonb."""
    from psycopg.types.json import Json

    skip = {"duration_sec"}  # clips 테이블 소관 (clip_features 컬럼 아님)
    data = {k: (Json(v) if isinstance(v, (dict, list)) else v)
            for k, v in feats.items() if k not in skip}
    cols = ["clip_id", "feature_version", *data.keys()]
    vals = [clip_id, feature_version, *data.values()]
    placeholders = ", ".join(["%s"] * len(cols))
    updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in data.keys())
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO public.clip_features ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT (clip_id, feature_version) DO UPDATE SET {updates}",
            vals,
        )
    conn.commit()


if __name__ == "__main__":
    import json

    if len(sys.argv) < 2:
        sys.exit("usage: feature_extractor.py <video_path> [--semantic] [--ocr]")
    out = extract_features(
        sys.argv[1],
        with_semantic="--semantic" in sys.argv,
        with_ocr="--ocr" in sys.argv,
        with_obs="--obs" in sys.argv,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
