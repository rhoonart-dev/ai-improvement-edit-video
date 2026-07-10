#!/usr/bin/env python
"""쇼츠 피처 추출 — 2트랙(Gemini VLM 자유버킷 + 결정론).  ★factory 독립 모듈.

원래 t3_extract/extract_v04.py의 추출 함수만 factory 안으로 이식(단일 진실원 이동).
run_factory가 여기서 import: deterministic_extract · fetch_description · vlm_extract
  · validate_buckets · BUCKETS.
- VLM 트랙: 10개 버킷 {note,tags,salience[,ai_features]} (gemini).
- 결정론 트랙: ffprobe·PySceneDetect·ffmpeg silencedetect/volumedetect·cv2.
CLI·Storage·manifest·타깃선정은 이식 안 함(그건 run_factory/db가 담당). 키는 config가 공급.
"""
import json
import os
import re
import subprocess
import time
from statistics import pvariance

# 10개 버킷. ai_features를 중첩하는 버킷과 그 필드.
BUCKETS = ["hook_0_3s", "build", "payoff", "subtitle_style", "tts_narration",
           "audio_design", "point_elements", "cover_title", "content_context",
           "scene_observation"]
AI_FEATURE_BUCKETS = {
    "hook_0_3s":         ["hook_semantic_strength", "hook_type", "question_hook", "face_count_first3s", "score_reason"],
    "build":             ["narrative_completeness", "dialogue_density", "action_density", "topic_shift_count", "scene_sequence", "score_reason"],
    "payoff":            ["climax_included", "has_punchline"],
    "point_elements":    ["emotion_category", "emotion_arc", "score_reason"],
    "content_context":   ["main_characters"],
    "scene_observation": ["scene_type", "place_setting", "num_speakers", "motion_level", "conflict_present"],
}

VLM_PROMPT = """당신은 한국어 유튜브 쇼츠를 분석하는 '영상 구성 분석기'다.
이 쇼츠는 드라마·예능·영화·다큐 등을 편집자가 잘라 만든 클립이다.
원작 IP의 유명세·내재 드라마가 아니라 '편집이 만든 구조'를 본다(decouple).

[기본 템플릿 — 절대 보고하지 말 것]
거의 모든 쇼츠는 다음이 기본값이다: ①상단 2줄 타이틀 캡션 ②가운데 원본 영상 ③하단 작품 로고/출처 ④영상 하단 대사 자막 ⑤BGM/효과음 = 상수. ⑥원본 대사 위 편집자 TTS가 흔히 개입(단 항상은 아님).
이 '기본의 존재'는 당연하므로 절대 적지 마라. 단 'TTS 없음'은 일탈이므로 반드시 적는다.

주어진 쇼츠 영상 전체(영상+오디오)를 보고/듣고, 아래 10개 버킷을 채워 JSON 하나로 출력하라.
각 버킷은 두 부분으로 답한다:
[1] 자유 서술 3필드 (모든 버킷 공통)
  - note: ★모든 버킷 최소 3문장. (1) 화면·소리에 실제로 있는 것을 구체적·자세·객관적으로 묘사(없는 것 지어내기 금지, 본 것만).
    (2) 이어서 그 편집 구성이 시청자 반응(유지·이탈·클릭·감정)에 어떤 효과를 불러일으킬 수 있는지 적는다. 기본 템플릿 존재 서술 금지, enum 환원 금지, 공허한 반복 금지.
  - tags: 짧은 키워드 3~8개. 같은 개념엔 매번 같은 단어.
  - salience: 0~1. 기본 템플릿 대비 일탈도(distinctiveness). 단순 존재 아님.
[2] ai_features (아래 표시된 버킷만) — 고정 스키마를 정확히 따른다. enum은 보기 중 하나, 점수는 0~1, bool/int/배열 형식대로.
  확인 안 되면: 점수 추정 자제, enum은 가장 가까운 값 또는 "기타", bool은 애매하면 false, 배열은 빈 배열.
  ★점수(0~1)가 있는 버킷(hook_0_3s·build·point_elements)은 ai_features에 "score_reason"(그 점수들을 왜 그 값으로 매겼는지 1~2문장, 앵커 대비 구체 근거, 가능하면 타임코드 t)을 반드시 포함. 높은 점수일수록 왜 그런지 정당화.

[ai_features 점수 앵커 — 냉정하게 변별, 중앙값 0.4~0.5]
  hook_semantic_strength: 0.2=밋밋/0.5=평범/0.8=즉시 궁금증·충격/1.0=스크롤 절대정지
  narrative_completeness: 0.2=맥락없으면 이해불가/0.5=대충이해/0.8=앞뒤없이 완결/1.0=완벽한 미니스토리
  dialogue_density/action_density: 0~1 실제 밀도대로 ; emotion_arc valence/arousal: 0~1
  climax_included: 명확한 감정/서사 절정(긴장→해소/펀치라인/감정 피크)이 실제로 담겼는가(애매하면 false)

버킷 (서사 3박자 hook→build→payoff + 나머지):
- hook_0_3s (retention): 첫 0~3초로 무엇을 거는가. ai_features:{hook_semantic_strength(0~1), hook_type(question|shock|preview|emotion|none), question_hook(bool), face_count_first3s(int), score_reason("hook_semantic_strength를 왜 그 값으로 매겼는지+t")}
- build (retention): hook 이후 전개. 시각 편집기법 일탈(punch-in/스피드램프/전환/프리즈/분할화면) 구체적으로. ai_features:{narrative_completeness(0~1), dialogue_density(0~1), action_density(0~1), topic_shift_count(int), scene_sequence([{t0,t1,desc}]), score_reason("narrative_completeness·dialogue·action 점수 근거+t")}
- payoff (engagement): 마무리(궁금증 해소/떡밥/CTA/루프/완결·열린·반전). ai_features:{climax_included(bool), has_punchline(bool)}
- subtitle_style (retention): 하단 자막 강조방식·이중자막·괄호지문·폰트. (ai_features 없음)
- tts_narration (retention): 편집자 TTS 레이어. 있으면 역할·비중·말투·원본대사 관계·목소리·발화 속도(빠름/보통/느림)를 note에. 없으면 'TTS 없음'+무엇이 대신하나. (ai_features 없음)
- audio_design (retention): 원본대사 살린/죽인 정도+BGM 무드+효과음·음량. 대사 대비 BGM 상대 음량 균형(대사 묻힘/BGM 존재감)도 구체적으로. (ai_features 없음)
- point_elements (engagement): 노리는 감정/소구(공감·사이다·코믹·카타르시스·짠함·반전·정보효용). ai_features:{emotion_category(분노|감동|유머|긴장|놀람|슬픔|평온|기타), emotion_arc([{t,valence,arousal}]), score_reason("emotion_arc 궤적을 왜 그렇게 찍었는지+t")}
- cover_title (ctr): 화면 상단 캡션 문구 전략(질문/도발/충격/티저/정보/대비)·색강조+랜덤 썸네일 인상. 화면 레이아웃 일탈(영상이 화면 전체를 채우는 풀블리드 vs 상하 여백 있는 박스형, 여백 처리: 검정/블러/색)도 여기에 구체적으로. (ai_features 없음)
- content_context (context): 원작 소여. ★장르는 `genre` 필드에 멀티라벨 배열로 따로 뺀다(예: ["스릴러","미스터리"] / ["코미디"] / ["다큐"], 순서 무의미, 장르 라벨만). tags엔 장르 제외 맥락만(제작퀄·IP/배우 인지도·포맷·명장면·톤). 출력 형태 = {note, genre:["..."], tags:["..."], salience, ai_features:{main_characters([string])}}
- scene_observation (context): 객관 장면 사실. ai_features:{scene_type(대화|액션|리액션|정보전달|몽타주|기타), place_setting(실내-가정|실내-스튜디오|야외|기타), num_speakers(int), motion_level(낮음|보통|높음), conflict_present(bool)}

규칙: ①10개 버킷 모두 자유서술 3필드 채운다. ②ai_features는 위 표시된 6개 버킷만, 고정 스키마대로. ③순서는 note→tags→salience→ai_features. ④설명문·마크다운·코드펜스 금지. 출력 JSON에 10개 버킷 키 + "onscreen_title_text" + "overall_confidence"(0~1). onscreen_title_text = 화면 상단 고정 캡션(화면 속 제목) verbatim(2줄이면 \\n, 없으면 빈 문자열; 하단 대사자막은 제외)."""


# ════════ 결정론 트랙 — 순수 집계함수 + 입력 공급(ffmpeg/scene/cv2) ════════

# --- ai-improvement/extract/feature_aggregates.py 에서 포팅(순수, 의존 없음) ---
def cut_metrics(scene_starts, duration_sec):
    """장면 시작 시각(초) → cut_count/avg_shot_len_sec/cut_rhythm_var/transition_count/hook_timing_sec."""
    if not duration_sec or duration_sec <= 0:
        return {"cut_count": None, "avg_shot_len_sec": None, "cut_rhythm_var": None,
                "transition_count": None, "hook_timing_sec": None}
    starts = sorted(s for s in scene_starts if 0 <= s <= duration_sec)
    if not starts or starts[0] > 0:
        starts = [0.0] + starts
    bounds = starts + [float(duration_sec)]
    shot_lens = [b - a for a, b in zip(bounds, bounds[1:]) if b > a]
    cut_count = max(len(shot_lens) - 1, 0)
    avg = sum(shot_lens) / len(shot_lens) if shot_lens else None
    var = pvariance(shot_lens) if len(shot_lens) > 1 else 0.0
    hook = starts[1] if len(starts) > 1 else None
    return {"cut_count": cut_count,
            "avg_shot_len_sec": round(avg, 3) if avg is not None else None,
            "cut_rhythm_var": round(var, 4),
            "transition_count": cut_count,
            "hook_timing_sec": round(hook, 3) if hook is not None else None}


def ratios_from_silencedetect(ffmpeg_stderr, duration_sec):
    """ffmpeg silencedetect stderr → {silence_ratio, speech_ratio}."""
    if not duration_sec or duration_sec <= 0:
        return {"silence_ratio": None, "speech_ratio": None}
    durs = [float(x) for x in re.findall(r"silence_duration:\s*([\d.]+)", ffmpeg_stderr or "")]
    sil = min(max(sum(durs) / duration_sec, 0.0), 1.0)
    return {"silence_ratio": round(sil, 6), "speech_ratio": round(1.0 - sil, 6)}


def ocr_subtitle_density(per_frame_has_text):
    """샘플 프레임 자막 유무(bool 리스트) → 자막 밀도(0~1). 빈 입력이면 None."""
    flags = list(per_frame_has_text)
    if not flags:
        return None
    return round(sum(1 for f in flags if f) / len(flags), 4)


# --- 입력 공급 ---
def ffprobe_duration(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=60)
        return float(out.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired):
        return None


def scene_starts(path):
    """PySceneDetect 장면 시작 시각(초) 리스트. cut_metrics 입력."""
    try:
        from scenedetect import open_video, SceneManager
        from scenedetect.detectors import ContentDetector
        video = open_video(str(path))
        sm = SceneManager()
        sm.add_detector(ContentDetector(threshold=27.0))
        sm.detect_scenes(video)
        return [s[0].get_seconds() for s in sm.get_scene_list()]
    except Exception:
        return []


def silencedetect_stderr(path):
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path),
             "-af", "silencedetect=noise=-30dB:d=0.5", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120)
        return proc.stderr
    except subprocess.TimeoutExpired:
        return ""


# NOTE: volume_dynamics 제거됨 — ai-improvement schema.sql엔 컬럼이 있으나 feature_extractor.py가
#       실제로 추출하지 않는 '스키마 전용' 피처(EDIT_DET에도 없음). v04_parking_lot.md 참조.
#       단 loudness_dynamics(v0.3 계열)는 v0.4.1에서 example-bank 고유로 부활 —
#       ai-video 오디오 믹스 게인(tts/original/bgm_gain_db) 레버에 대응하는 정량 신호가 없었기 때문.


def loudness_dynamics_db(path):
    """★v0.4.1(example-bank 고유, 파킹랏 부활). ffmpeg volumedetect →
    peak-to-average dB(max_volume - mean_volume). ai-video 믹스 게인 레버 대응 프록시."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path),
             "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120)
        mean = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", proc.stderr or "")
        peak = re.search(r"max_volume:\s*(-?[\d.]+)\s*dB", proc.stderr or "")
        if mean and peak:
            return round(float(peak.group(1)) - float(mean.group(1)), 2)
    except subprocess.TimeoutExpired:
        pass
    return None


def video_fill_ratio_cv2(path, n_samples=20):
    """★v0.4.1(example-bank 고유). 실영상 영역/캔버스 비율 0~1.
    샘플 프레임들의 시간축 픽셀 변화(std) 마스크로 '움직이는 영역'의 바운딩 행×열 비율을 계산 —
    정적 여백·상단 캡션·로고·워터마크는 제외된다. 풀블리드≈1.0,
    ai-video 박스형 기본(1080x1920 캔버스 중 800x1100)≈0.42.
    한계: 여백을 '움직이는 블러 배경'으로 채우면 풀블리드로 잡힘(레어 케이스, VLM note가 보완)."""
    try:
        import cv2
        import numpy as np
    except Exception:
        return None
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total < 3:
        cap.release()
        return None
    idxs = [int(i * (total - 1) / max(n_samples - 1, 1)) for i in range(min(n_samples, total))]
    frames = []
    for ix in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, ix)
        ok, frame = cap.read()
        if ok:
            g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            frames.append(cv2.resize(g, (96, 160)).astype("float32"))
    cap.release()
    if len(frames) < 3:
        return None
    std = np.std(np.stack(frames), axis=0)      # (160,96) 픽셀별 시간축 변화량
    active = std > 6.0                          # 움직이는 픽셀
    row_act = active.mean(axis=1) > 0.2
    col_act = active.mean(axis=0) > 0.2
    if not row_act.any() or not col_act.any():
        return 0.0
    rows = np.where(row_act)[0]
    cols = np.where(col_act)[0]
    return round(((rows[-1] - rows[0] + 1) / active.shape[0])
                 * ((cols[-1] - cols[0] + 1) / active.shape[1]), 4)


def fetch_description(sid):
    """★v0.4.1(example-bank 고유). 발행 설명문 원문(yt-dlp 메타만, 다운로드 없음).
    ai-video candidate.description 대응 — 수집만. 실패 시 None."""
    try:
        r = subprocess.run(["yt-dlp", "--no-warnings", "--no-update", "--skip-download",
                            "--no-playlist", "--socket-timeout", "30",
                            "--print", "description",
                            f"https://www.youtube.com/watch?v={sid}"],
                           capture_output=True, text=True, timeout=90,
                           encoding="utf-8", errors="replace",
                           # Windows 콘솔 cp949로 새는 것 방지 — yt-dlp(파이썬) 출력 UTF-8 강제
                           env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        if r.returncode == 0:
            d = (r.stdout or "").strip()
            return d or None
    except subprocess.TimeoutExpired:
        pass
    return None

_TEXT_HELP = None


def _text_boxes(gray):
    """cv2 형태학 기반 가로 텍스트영역 검출(OCR 아님, 유무만)."""
    import cv2
    H, W = gray.shape
    grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    _, bw = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    closed = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (15, 1)))
    cnts, _h = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if 8 <= h <= H * 0.18 and w >= 25 and (w / (h + 1e-6)) >= 1.8 and (w * h) >= W * H * 0.0006:
            boxes.append((x, y, w, h))
    return boxes


def subtitle_density_cv2(path, sample_interval_sec=1.0):
    """번인 자막 OCR 대역(cv2): 하단 40% 텍스트영역 유무 → subtitle_density(0~1).
    ai-improvement extract_burned_subtitles와 같은 산출(easyocr 대신 cv2 휴리스틱)."""
    try:
        import cv2
    except Exception:
        return None
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(int(fps * sample_interval_sec), 1)
    flags, i = [], 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % step == 0:
            h = frame.shape[0]
            crop = frame[int(h * 0.6):, :]                    # 하단 40%
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            flags.append(len(_text_boxes(gray)) > 0)
        i += 1
    cap.release()
    return ocr_subtitle_density(flags)


_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF←-⇿⌀-⏿⬀-⯿]")


def _title_meta(title):
    if not title:
        return {"title_text": None, "hashtags": None}
    return {"title_text": title,
            "hashtags": [t.rstrip(",") for t in re.findall(r"#\S+", title)]}


def title_embedding(text, key):
    """Gemini text-embedding (768d). --embed 시에만. 실패/미설치 시 None."""
    if not text or not key:
        return None
    try:
        from google import genai
        client = genai.Client(api_key=key)
        r = client.models.embed_content(
            model="text-embedding-004", contents=text,
            config={"output_dimensionality": 768})
        emb = r.embeddings[0].values
        return [round(float(x), 6) for x in emb]
    except Exception:
        return None


def deterministic_extract(path, title=None, key=None, do_embed=False):
    """ai-improvement 결정론 피처 set (v0.4 비-VLM 트랙)."""
    dur = ffprobe_duration(path)
    rec = {"duration_sec": round(dur, 3) if dur else None}
    rec.update(cut_metrics(scene_starts(path), dur))
    rec.update(ratios_from_silencedetect(silencedetect_stderr(path), dur))
    rec["loudness_dynamics"] = loudness_dynamics_db(path)          # v0.4.1
    try:
        rec["subtitle_density"] = subtitle_density_cv2(path)
    except Exception as e:
        rec["subtitle_density"] = None
        rec["_subtitle_note"] = f"ocr_failed:{str(e)[:80]}"
    try:
        rec["video_fill_ratio"] = video_fill_ratio_cv2(path)       # v0.4.1
    except Exception:
        rec["video_fill_ratio"] = None
    rec.update(_title_meta(title))
    rec["title_embedding"] = title_embedding(title, key) if do_embed else None
    return rec


# ════════ VLM (Gemini) ════════
def vlm_extract(path, key, model):
    from google import genai
    client = genai.Client(api_key=key)
    up = client.files.upload(file=str(path))
    for _ in range(90):
        st = getattr(up.state, "name", str(up.state))
        if st == "ACTIVE":
            break
        if st == "FAILED":
            raise RuntimeError("file processing FAILED")
        time.sleep(1)
        up = client.files.get(name=up.name)
    # Gemini가 가끔 깨진 JSON을 뱉음(긴 출력) → 파싱 실패 시 1회 재생성.
    last_err, data = None, None
    for _ in range(2):
        resp = client.models.generate_content(
            model=model, contents=[up, VLM_PROMPT],
            config={"response_mime_type": "application/json", "temperature": 0.3})
        txt = (resp.text or "").strip()
        if txt.startswith("```"):
            txt = txt.strip("`")
            txt = txt[4:].strip() if txt.lower().startswith("json") else txt
        try:
            data = json.loads(txt)
            break
        except json.JSONDecodeError as e:
            last_err = e
    try:
        client.files.delete(name=up.name)
    except Exception:
        pass
    if data is None:
        raise last_err
    if isinstance(data, list):
        data = next((x for x in data if isinstance(x, dict)), {})
    return data


def validate_buckets(data):
    issues = []
    for b in BUCKETS:
        v = data.get(b)
        if not isinstance(v, dict):
            issues.append(f"{b} 누락/형식오류")
            continue
        if not isinstance(v.get("tags"), list):
            issues.append(f"{b}.tags 형식오류")
        if "salience" not in v:
            issues.append(f"{b}.salience 없음")
        if b in AI_FEATURE_BUCKETS:
            af = v.get("ai_features")
            if not isinstance(af, dict):
                issues.append(f"{b}.ai_features 누락")
            else:
                miss = [k for k in AI_FEATURE_BUCKETS[b] if k not in af]
                if miss:
                    issues.append(f"{b}.ai_features 필드없음:{miss}")
    return issues

