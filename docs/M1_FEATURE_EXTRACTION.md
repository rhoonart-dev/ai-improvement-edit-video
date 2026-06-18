# M1 — 피처 x 추출 어댑터 (ai-video 모듈 재사용) + moat 검증

> 설계 근거 [SELF_IMPROVEMENT_SPEC.md](SELF_IMPROVEMENT_SPEC.md) §2·§5-2, [M0_ETL.md](M0_ETL.md).
> ai-video 레포: `/Users/gimsewon/rhoonart/ai-video`. youtube_studio엔 y만 있으므로 **x는 영상 직접 분석**.

## 두 경로
| 클립 종류 | x 출처 | 비용 |
|---|---|---|
| **기존(laeebly, source='existing')** | 영상 파일을 ai-video 분석 모듈로 직접 분석 | 다운로드+분석 (M1은 표본만) |
| **우리 산출물(source='auto_edit')** | ai-video 산출 JSON에서 공짜 (`checkpoint_*.json`·`edit_plan.json`·`run_log.json`) | provenance 연결만 |

영상 취득: ai-video `app/modules/youtube_downloader.py`로 content_id → 파일 (분석 한정·재발행 금지, ToS).

## ai-video 모듈 → clip_features 매핑
| 모듈 (ai-video) | 함수 | 산출 피처(클래스) |
|---|---|---|
| `media_probe` | `probe_media()→MediaInfo` | duration_sec, fps, has_audio (A/B) |
| `scene_detect` | `detect_scenes()→[Scene]` | cut_count, avg_shot_len_sec, cut_rhythm_var, transition_count, hook_timing_sec (B) |
| `speech` + **ffmpeg silencedetect** | ⚠️ ai-video `extract_vad_segments`는 **스텁(`return []`)** → silencedetect로 직접 산출 | silence_ratio, speech_ratio(=비침묵, BGM 포함 근사) (D) |
| `ffmpeg_utils` (ebur128) | 라우드니스 | volume_dynamics, bgm_present, bgm_energy (D/B) |
| **OCR (신규)** | 번인 자막 추출 | subtitle_density, subtitle_style (B) — 기존 영상은 자막이 번인됨 |
| `face_id` | `FaceIdentifier` | 얼굴 점유/클로즈업 (raw_features) |
| `gemini_client` | `analyze_chunk()` (≤60s는 단일 chunk) | narrative_completeness, climax_included, hook_semantic_strength, dialogue_density, action_density, emotion_arc, scene_sequence, main_characters (C) |
| (publish meta) | laeebly video_title / 임베딩 | title_text, title_embedding, hashtags (E) |

> **신규 컴포넌트 = 번인 자막 OCR.** 기존 클립은 자막이 영상에 구워져 있어 `subtitle.parse_*`(파일 파싱)로는
> 안 잡힘 → 프레임 OCR 필요(GROUNDED 지적). 우리 산출물은 ASS 자막 메타가 있어 불필요.

## Gemini 2.5 Pro scene-observation (C)
- ≤60s Shorts는 **단일 chunk** → `analyze_chunk` 스타일 1회 호출. `GEMINI_PROMPT_TEMPLATE`(gemini_client.py:51)
  의 candidate_moments 추출 로직을 **관찰 전용**으로 변형(편집 결정 말고 구조 기술), SPEC §2-C 프롬프트 골격 적용.
- decouple 지시(원작 유명세 배제) + 타임코드 근거 의무 + JSON schema 고정.

## 어댑터 인터페이스 (이 레포에 신규)
```
extract/feature_extractor.py
  extract_features(video_path, meta) -> dict   # clip_features 컬럼에 1:1 매핑
    = {**deterministic(media_probe,scene_detect,speech,ebur128,ocr,face_id),
       **semantic(gemini analyze_chunk),
       **packaging(meta.title → embedding)}
  → upsert clip_features(clip_id, feature_version, ...)
```
`feature_version` = ai-video git_sha + 프롬프트 버전 + 임베딩 모델 (재현성). 의존: ffmpeg·faster-whisper·
pyscenedetect·deepface·Gemini API (ai-video requirements 재사용).

## feature_registry (DB 시드 완료)
제어가능 피처 필터(§5-2)의 단일 진실원. 32개 시드 적재 — 각 `controllable` + `control_surface`(ai-video knob/프롬프트):
- **제어가능(actionable)**: duration_sec(`AppConfig.target_duration_sec` 40~60), hook_timing_sec(`STORY_COMPOSITION_PROMPT`),
  subtitle_density(`subtitle_max_chars_per_line`), bgm_energy(`bgm_gain_db`), ending_type(story patterns),
  narrative_completeness/climax/hook_semantic(`select_diverse_storylines`+story prompt), speech/silence_ratio(`silence_cutter`) 등.
- **통제(불변)**: ip_popularity, subscriber_count, dialogue/action_density, scene_sequence, main_characters 등.

## 표본 (M1)
전수 60k 분석은 비용 과다 → **Shorts(≤180s) ∩ +14일가능 에서 장르(작품)×성과사분위 층화 2~5k**.
(검증 적재로 파이프라인은 입증됨 — clips/clip_performance/v_training_matrix 동작 확인.)

## M1 moat 검증 게이트 (go/no-go)
1. baseline `B`: 컨텍스트(채널·작품 EB·발행시기·duration)만으로 +14일 성과 예측.
2. edit 피처 x(B/C/D/E) 추가 시 **out-of-sample 설명력 상승(ΔR²/ΔAUC, nested CV + 순열검정)** 측정.
3. 유의·비자명 uplift → 전제(편집이 성과를 가른다) 지지 → M2(judge)/M3(리워드 모델) 진행. ≈0 → 재고.

## EVALUATOR 교체 연결 (M4 예고)
현 ai-video 휴리스틱 평가자(`AppConfig.viral_score_min_threshold`·`viral_scroll_stop_threshold` +
`select_diverse_storylines`)를 **학습 리워드 모델(R̂+Q̂+recall@k)**로 교체 → 빠른 루프(SPEC §4-3).

## 다음
1. `extract/feature_extractor.py` 구현(ai-video 모듈 import + OCR 신규) → 표본 2~5k `clip_features` 적재.
2. baseline B + nested CV로 M1 게이트 판정.
