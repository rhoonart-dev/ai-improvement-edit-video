# Grounded Design — 실코드 기반 닫힌 루프 (2026-06-14 세션)

> 이 문서는 [ARCHITECTURE.md](ARCHITECTURE.md) · [DATA_SCHEMA.md](DATA_SCHEMA.md) ·
> [EVALUATION.md](EVALUATION.md) · [DATA_COLLECTION.md](DATA_COLLECTION.md) ·
> [ROADMAP.md](ROADMAP.md)(이상 2026-06-10 작성)을 **대체하지 않고 고정(ground)** 한다.
> 위 문서들이 "플라이휠 비전"이라면, 이 문서는 **이미 존재하는 `ai-video` 실코드 + laeebly 실데이터 +
> 오늘 확정한 결정**에 비전을 못박은 실행 설계다.

---

## 0. 기존 문서 대비 갱신된 전제 3가지 ⭐ (먼저 읽을 것)

| 전제 | 2026-06-10 문서 | 갱신 (2026-06-14 확정) | 영향 |
|------|-----------------|------------------------|------|
| **GENERATOR** | 후보생성·이해 모듈을 **새로 구축**(그린필드) | `ai-video` 14단계 파이프라인이 **이미 그 GENERATOR다**. 새로 짓지 않고 **개선 대상**으로 삼음 | 작업량 급감 — §1 매핑 |
| **시드 데이터** | "없음(백지). AI가 인터넷에서 자율 수집" | laeebly `youtube_studio`에 **4.56M행 / 60k영상 / 296채널**의 실성과가 이미 존재 → **시드가 풍부** | 콜드스타트 위험 급감 — §2 |
| **발행/루프** | "발행 0번 · 오프라인 학습·평가 한정" | 오프라인 벤치마크는 유지하되, **우리 출력물은 단일 채널에 발행해 닫힌 루프**(온라인 A/B)를 돈다 | §5. 단, 스크랩 IP **재발행 금지 원칙은 유지** |

> 외부 우승작의 ground-truth 성과는 **이미 laeebly DB에 있으므로 발행 없이 확보**된다.
> 발행이 필요한 건 오직 **우리 자신의 산출물 성과**뿐이고, 그래서 **단일 채널 파일럿**으로 시작한다.

---

## 1. 대전제: `ai-video`는 이미 존재하는 GENERATOR다

기존 ARCHITECTURE.md의 박스를 `ai-video` 실모듈에 매핑하면, **새로 지을 부분이 명확히 좁혀진다.**

| ARCHITECTURE.md 박스 | `ai-video` 실제 구현 (ai-video repo) | 신규 구축? |
|----------------------|--------------------------------------|-----------|
| COLLECTOR | — | **신규** (ingest: laeebly + 우리 출력) |
| PREPROCESS | `media_probe.py` · `speech.py`(Whisper) · `scene_detect.py` · `face_id.py`(ArcFace) · `subtitle.py` | 재사용 |
| ALIGNER (클립↔원본 정렬) | — | **신규** (오디오지문+자막) |
| UNDERSTAND | `gemini_client.analyze_chunk`(Pro) + `video_intent`(전체 영상 파악, PR-7 신설) | 재사용 |
| GENERATOR | `analyze_chunk`의 `candidate_moments` + `compose_story_with_context`(Flash) + `story_builder.py` | 재사용 |
| EVALUATOR | 현재 = `viral_score`/`score` 휴리스틱 + `select_diverse_storylines` → **학습 리랭커로 교체** | 개선 |
| BENCHMARK | — | **신규** (recall@k vs laeebly 우승작) |
| IMPROVER | — | **신규** (자가개선 파이프라인) |
| RENDER | `renderer.py`(9:16·자막 번인·TTS·얼굴추적) | 재사용 |

**결론**: 신규로 지을 것은 **COLLECTOR · ALIGNER · BENCHMARK · 학습형 EVALUATOR · IMPROVER**.
PREPROCESS / UNDERSTAND / GENERATOR / RENDER는 `ai-video`를 그대로 호출·개선한다.

---

## 2. ① 데이터 수집 & 정제 — laeebly가 시드다

### 2-A. 소스 & 보상 신호 (실데이터)

| 소스 | 가져오는 것 | 비고 |
|------|-------------|------|
| **laeebly `youtube_studio`** (project `mehvzxzajydffflqcuuk`) | 채널내 백분위 good/bad, `impression_click_rate`(=CTR, %), 조회수, `watch_time_hours` | **CTR이 DB에 이미 존재** → API 불필요 |
| 리텐션 도출 | `watch_time_hours*3600 / views / video_length` | DB만으로 계산 (`video_length`는 varchar 초) |
| YouTube Data API | 외부 벤치마크(views/likes/comments) | laeebly 밖 우승작 다양성 |
| 우리 `ai-video` 출력 | checkpoint/edit_plan/run_log + 발행 후 성과 | 피처는 **공짜**(§2-C) |

**보상(reward)** = `0.5·리텐션 + 0.3·CTR + 0.2·조회수` (채널 내 백분위 정규화), **이중 라벨**(초기 48~72h + 성숙 **14d**, 2026-06-16 변경: 기존 28d → §10-A).
→ 이는 [EVALUATION.md](EVALUATION.md)의 3층 보상 중 **Ground-truth 층을 laeebly 수식으로 구체화**한 것. 오프라인 recall은 그대로 1차 목적함수로 유지.

### 2-B. 영상 콘텐츠 분석 — "구조·형태·스토리·분위기·오디오·자막"

> 결정적 설계: **`ai-video`의 분석 모듈을 그대로 재사용**해 외부 우승작을 분석한다.
> 그러면 우리 출력물과 **완전히 같은 축**으로 비교돼 "우승작은 왜 이기나"를 계산할 수 있다.

| 피처군 | 신호(예) | 추출기 (재사용) |
|--------|----------|----------------|
| 구조 | duration, 컷 수, 평균 샷 길이, hook/build/payoff, 클리프행어 | `scene_detect` + Gemini Pro |
| 형태 | 얼굴 점유율, 화자 수, 줌/크롭 다이내믹, 화면텍스트 밀도 | `face_id` · `reframe` |
| 스토리 | 토픽, 감정 아크, hook/payoff 텍스트, 요약 | Gemini Pro (`analyze_chunk` 스타일) |
| 분위기 | 밝기/색 팔레트, 템포, 장르 톤 | ffmpeg + Gemini |
| 오디오 | BGM 유무, 라우드니스 곡선, 무음 비율, TTS/원음 비율, 말속도 | ffmpeg `ebur128` + `speech` |
| 자막 | 캡션 스타일, 초당 글자수, 커버리지, 강조 밀도, **(예능)번인 OCR** | `subtitle` + OCR(신규) |

### 2-C. 우리 출력물은 재분석 불필요

`ai-video`가 남기는 산출물에서 피처가 **이미 구조화돼 있다**:
`checkpoint_story.json`(hook/build/payoff·tts_cues) · `edit_plan.json` · `crop_*.json` · `run_log.json`.
→ 우리 출력물은 **provenance 연결만** 하면 피처 확보 끝. 외부 우승작만 §2-B로 분석.

### 2-D. 산출물 — 경험 데이터셋 1행

[DATA_SCHEMA.md](DATA_SCHEMA.md)의 `Candidate`/`Outcome`/`Experiment`를 따르되, provenance를 추가:

```jsonc
ExperienceRecord {
  id, source: "external" | "ours",
  provenance: { content_id, channel_id, ai_video_run_id?,
                prompt_versions?, config_hash?, reranker_version?, uploaded_at },
  features: { structure{...}, form{...}, story{...}, mood{...}, audio{...}, subtitle{...} },
  performance: { early{retention,ctr,views @48-72h}, mature{... @14d} },
  reward: 0.0~1.0, label: "good"|"bad"|"mid"   // 채널 내 백분위
}
```

### 2-E. 리서치 제안 — 추가하면 좋은 신호 (쇼츠 성과 설명력 순)

1. **초당 시청 유지 곡선(audienceRetention)** — 쇼츠는 "어디서 이탈하나"가 전부. **단일 최고가치 추가.** 우리 컷·hook 피처와 곡선을 맞추면 인과가 보인다.
2. 평균 시청 비율 / 평균 시청 시간 — 후킹·길이 튜닝 직접 타깃
3. 3초 유지율 / 스와이프 이탈률 — hook 품질 프록시
4. 재생 소스(쇼츠 피드 vs 탐색) — 노출 알고리즘 반응
5. 좋아요·댓글·공유·루프(재시청) — 참여·바이럴

---

## 3. 전제 — Phase 0: Provenance (단일 채널 파일럿 범위)

닫힌 루프는 "이 쇼츠를 만든 **버전**" ↔ "그 쇼츠의 **실측 성과**"를 잇지 못하면 성립하지 않는다.

| 추가 | 위치 (ai-video repo) | 내용 |
|------|----------------------|------|
| 버전 스탬프 | `run_log.json` | `prompt_versions{analyze,story,relation}` · `config_hash` · `reranker_version` · `git_sha` |
| 업로드 연결키 | 발행 시 | 결과 쇼츠 → YouTube `content_id`(11자) ↔ `ai_video_run_id` 매핑 |
| 변경 단위화 | ai-video repo | 인라인 프롬프트 문자열 → **버전 가능한 아티팩트**로 분리 |

**파일럿 범위**: laeebly 296채널 중 **업로드량 많고·성과 성숙·장르 대표성** 있는 **1개 채널**만 우선 배선·발행·A/B. 보상이 채널 내 백분위라 단일 채널이 자연스럽다.

---

## 4. ③ 자가개선 — 진단 → 라우터 → 생성 → 평가 → 승급

### 4-A. 진단 (판단 기준조차 데이터로)

손으로 룰을 박지 않고, **reward를 예측하는 모델의 피처 중요도(SHAP 등)**로 "어떤 피처가 성과를 가르나"를 발견 → 가설 생성. 예: *"우승작 hook=첫 2초 질문형 / 우리 평균 5초", "우승작 22~28s / 우리 target 50s"*.

### 4-B. 라우터 — 어떤 레버로 고칠까

```
개선 가설 1건
   │  지시로 표현 가능한 행동인가?
   ├─ 예 → 지시하면 모델이 따르나?(프롬프트 프로브)
   │        ├─ 따름     → ▶ 프롬프트 레버 (GEPA)            [최저비용·L0]
   │        └─ 못 따름  → ▶ 학습 레버 후보
   ├─ 수치 knob?(길이/임계치/후보수) → ▶ 설정 튜닝(베이지안)  [L1]
   └─ 후보는 좋은데 "선택·랭킹"이 약함?
            └─ 예 → ▶ 학습형 리랭커/리워드 (→ §6 파인튜닝)   [L2]
                         └─ 생성기 자체 한계 → ▶ 생성기 교체/FT [L3]
```
라우터의 "이 레버가 실제로 먹혔나" 임계치도 **과거 A/B 이력에서 캘리브레이션**(메타학습) → 안 먹히면 자동 에스컬레이션. (= ARCHITECTURE.md의 L0~L3 사다리를 라우팅 규칙으로 구체화)

### 4-C. 개선이 내려앉는 실제 표면 (ai-video repo, file:line)

| 표면 | 위치 | 레버 |
|------|------|------|
| 청크분석 프롬프트(Pro) | `app/modules/gemini_client.py:51` `GEMINI_PROMPT_TEMPLATE` | 프롬프트 |
| 스토리 프롬프트(Flash) | `app/modules/gemini_client.py:470` `STORY_COMPOSITION_PROMPT` | 프롬프트 |
| 가편집 검수 프롬프트(Pro) | `app/modules/gemini_client.py:1361` `AV_ALIGN_PROMPT` (PR-7 신설; 관계추출 프롬프트는 삭제됨) | 프롬프트 |
| 설정 knob 다수 | `app/config.py:59` `AppConfig` (필드 ~59개: `viral_score_min_threshold`·`target_duration_sec` 등) | 설정 |
| 후보 스코어/선택 | `app/pipeline.py:330` `_dedup_overlapping_candidates` · `app/modules/story_builder.py:65` `select_diverse_storylines` | 학습 리랭커 |

### 4-D. 평가자 & 승급

- 오프라인: [EVALUATION.md](EVALUATION.md)의 recall@k/IoU + LLM-judge 앙상블(pairwise) + 학습 리워드, 실측 KPI **캘리브레이션** 필수.
- **Goodhart 방어**: 앙상블 + 분포(KL) 가드 + **온라인 A/B를 최종 진실**로.
- 승급: `오프라인 통과 → 섀도우 → 단일채널 온라인 A/B → 유의 개선 시 승급 / 실패 시 자동 롤백`. **L0(HITL PR 승인)부터**.

---

## 5. 닫힌 루프 + 단일 채널 파일럿 (오프라인↔온라인 화해)

```
[수집·정제] laeebly 우승작(성과 DB 보유) + 우리 신규 출력(성과 성숙분)
      │
[오프라인] recall 벤치 + judge/리워드로 후보 가지치기   ← 매일 100× 반복(발행 0)
      │
[온라인 ] 단일 채널에 control(현 ai-video) vs treatment(개선판) A/B 발행
      │        (동시 슬롯 분할 또는 시계열 A/B)
[승급]   14d 성숙 성과로 승급/롤백 → 데이터셋·A/B 이력에 피드백 → 처음으로
```
오프라인은 **빠른 반복**, 온라인 단일채널은 **최종 진실**. 성과가 14일에 성숙하므로 **롤링 윈도우**로 동작.
**유지되는 원칙**: 스크랩한 드라마/영화/예능 IP 원본은 재발행 금지([DATA_COLLECTION.md](DATA_COLLECTION.md)) — 발행은 권리 확보된 **우리 채널 산출물** 한정.

---

## 6. ② 파인튜닝 — 3 티어 (Gemini SFT 불가 확인)

> ⚠️ **Gemini 3.x preview는 supervised fine-tuning 불가**(GA 2.5만, 일정 미정). CLAUDE.md gen-3 락과도 충돌.
> 따라서 학습 레버의 1차 타깃은 **Gemini가 아니라 소형 오픈모델**. 오늘 결정: **생성기 교체까지 검토**.

| 티어 | 대상 | 방법 | 리스크 | 도입 |
|------|------|------|--------|------|
| **T1 (근시일)** | 보조 **리워드/리랭커** | Qwen/Gemma + TRL/Unsloth, `features→reward` 학습 → 후보 선택 앞단 재랭킹 | 낮음 (생성기 무변경) | 프롬프트·설정 정체 시 |
| **T2 (중기)** | **스토리 작성기 교체** (Flash 역할, 텍스트) | 오픈 텍스트 모델 SFT→DPO, A/B vs Gemini-Flash | 중 (텍스트 SFT는 저렴, 단계 경계 명확) | T1 검증 후 **가장 낮은 리스크의 생성기 교체** |
| **T3 (장기)** | **멀티모달 분석기 교체** (Pro 역할) | 오픈 VLM(Qwen-VL/InternVL류) FT | 높음 (장尺·저fps 영상 이해 품질·GPU 인프라·gen-3 락 거버넌스) | T1·T2 정체 + 데이터가 정당화할 때만 |

**파이프라인**: `데이터셋 export → 학습 → 평가(held-out + 실측 캘리브레이션) → 레지스트리 등록(버전) → ai-video가 플래그 뒤에서 로드 → A/B`.
T2부터는 [ARCHITECTURE.md](ARCHITECTURE.md) L3(정책 파인튜닝)의 SFT→DPO 경로를 그대로 사용.

---

## 7. 리포지토리 구조 (제안)

```
ai-improvement-edit-video/
├─ provenance/   # Phase 0: ai-video에 주입할 버전스탬프·업로드 연결키 어댑터
├─ ingest/       # ① laeebly(supabase) · youtube_api · our_outputs 커넥터
├─ align/        # ALIGNER: 오디오지문+자막으로 클립↔원본 정렬 (신규)
├─ refine/       # ① 피처 추출(ai-video 모듈 재사용) + 보상 라벨러 + (예능)OCR
├─ dataset/      # 경험 데이터셋 스키마·버전 스토어 (DATA_SCHEMA.md 준수)
├─ benchmark/    # recall@k/IoU vs laeebly 우승작 (EVALUATION.md)
├─ improve/      # ③ diagnose / router / generators(gepa·config·reranker) / evaluate / promote
├─ finetune/     # ② T1 리랭커 → T2 텍스트 생성기 → T3 VLM, 레지스트리
└─ orchestrator/ # 배치 루프 스케줄러 (롤링 윈도우)
```

---

## 8. 오늘(2026-06-14) 확정된 결정 로그

- **저장 방식**: 이 설계를 문서로 저장(구현 보류). ← 본 문서.
- **학습 레버 범위**: **생성기 교체까지 검토** — T1 리랭커 → T2 텍스트 생성기 교체 → T3 멀티모달 VLM. (Gemini SFT 불가가 전제)
- **닫힌 루프 범위**: **단일 채널 파일럿**으로 온라인 A/B 시작.
- (오전 세션 계승) 보상 = 0.5/0.3/0.2 · 이중 라벨 · 자율성 L0→L3 · 레버 우선순위 프롬프트>설정>리랭커>FT · Phase 0 provenance 전제.

## 9. 기존 문서에 반영 권장 (사용자 확인 후)

- `README.md` "프로젝트 결정(확정)" 표: *"오프라인 우선·발행 0번"* → *"오프라인 벤치 + 단일채널 온라인 A/B"* 로 갱신.
- `EVALUATION.md`: Ground-truth 층에 laeebly 보상 수식(0.5/0.3/0.2) 명시.
- `DATA_COLLECTION.md`: "시드 없음" → "laeebly가 1차 시드(외부 다양성은 자율수집 보조)". 재발행 금지 원칙은 유지.
- `ROADMAP.md`: Phase 4(실발행)를 **단일 채널 파일럿**으로 구체화, Phase 5에 T2/T3 생성기 교체 트랙 추가.

---

## 10. 미해결 구조 리스크 (2026-06-16 코드 검증 리뷰)

> 골격(데이터 흐름·모듈 재사용·provenance·2-루프·레버 사다리)은 건전. **분석 모듈 재사용 키스톤은 코드로 검증됨** — `media_probe`/`speech`/`scene_detect`/`face_id`/`subtitle`/`reframe` + `analyze_chunk`(:1434)/`compose_story_with_context`(:1995)/`select_diverse_storylines`(:65) 실재. 아래는 **plumbing 이후 학습/개선 주장을 신뢰하기 전에** 닫아야 할 빈틈.

| # | 리스크 | 핵심 | 권고 |
|---|--------|------|------|
| **A** | 측정 대역폭 병목 | 외부 루프 진실 신호가 **단일 채널 A/B** 한 곳. 14d 단축으로 분산↑·검정력↓ | 착수 전 파워 계산 · 초기엔 오프라인 recall을 1차 신호로 · 채널 확장 검토 |
| **B** | 인과 귀인 미흡 | `refine→reward` 사이 **디컨파운딩 없음**(채널 백분위는 IP·썸네일/제목 못 잡음). CTR(0.3)은 편집기가 못 바꾸는 패키징. SHAP는 상관. 리랭커 off-policy | 정규화 컴포넌트 신설(IP·패키징·시기 통제) · CTR의 자리 결정 · 탐색 로깅 |
| **C** | 품질 가드 미배선 | `validator.py`(`validate_output`: 길이·오디오피크·블랙프레임)가 **보상 루프 게이트로 미연결**. 댓글 감성은 orphan | validator를 발행 전 하드 게이트로 · 댓글 감성 포함/제거 결정 |
| **D** | cadence가 원칙뿐 | "생성기 동결 ↔ 평가자 동결" 교대가 메커니즘 아님 | orchestrator 상태기계로 강제 |
| **E** | 개선 표면 맵 드리프트 | `14→12단계` 리팩터 후 §1·§4-C가 코드와 어긋남(관계추출 삭제·라인 이동·`av_align` 신설; 본 리뷰서 패치) | 표면을 **라이브 코드에서 자동 추출**(레지스트리/AST) · provenance를 코드 측까지 확장 |

**보상 성숙 28d→14d (2026-06-16 결정):** 외부 루프 가속이 목적. 트레이드오프 = 성과 미성숙·분산↑(리스크 A와 직결). §2-A·§2-D·§5 반영 완료.
