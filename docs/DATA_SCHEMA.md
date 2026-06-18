# Data Schema — 플라이휠의 토대

> 자기개선의 전제는 "모든 결정을 `(상태, 행동, 결과)`로 기록"하는 것이다.
> 이 스키마는 그 기록을 위한 최소 데이터 모델이다. 코드보다 먼저 이게 옳아야 한다.

## 엔티티 개요

```
Source(원본) 1──N Segment(분석 단위)
Source 1──N HumanClip(사람이 만든 클립 = 정답 라벨)
Source 1──N Candidate(AI가 생성한 후보 구간)
Candidate 1──N Score(평가자 점수, 평가자 버전별)
Candidate 0──1 Outcome(실제 발행 시 성과 — Phase 4+)
Experiment(파이프라인/평가자/모델 버전) 1──N Candidate, Score
모든 모듈 ──▶ DecisionLog
```

## Source — 롱폼 원본

| 필드 | 타입 | 설명 |
|---|---|---|
| `source_id` | id | |
| `platform` / `external_id` | str | 유튜브 등 + 원본 ID |
| `domain` | enum | drama / movie / variety / yt_longform |
| `duration_s` | float | |
| `published_at` | datetime | |
| `channel` | str | 공식 채널 여부 플래그 포함 |
| `feature_ref` | uri | 추출 피처/임베딩 저장 위치(객체스토리지) |
| `media_cache_ref` | uri\|null | **임시 캐시만. 영구저장·재배포 금지** |
| `license_note` | str | 저작권/ToS 메모 |

## Segment — 멀티모달 전처리 산출 (원본 1회)

| 필드 | 타입 | 설명 |
|---|---|---|
| `segment_id` / `source_id` | id | |
| `start_s` / `end_s` | float | |
| `transcript` | text | ASR(단어 타임스탬프) |
| `speaker` | str | 화자분리 결과 |
| `burned_caption` | text\|null | **편집자막 OCR (예능 핵심 신호)** |
| `audio_events` | json | laughter/applause/silence/bgm_swell ... + 강도 |
| `shot_boundary` | bool | 장면 전환 여부 |
| `faces` | json | 인물 bbox·표정·클로즈업 정도 |
| `emotion_vec` | vector | 멀티모달 감정 임베딩 |

## HumanClip — 사람이 만든 클립 (정답 라벨의 원천)

| 필드 | 타입 | 설명 |
|---|---|---|
| `human_clip_id` | id | |
| `source_id` | id | 어느 원본에서 나왔나 |
| `aligned_start_s` / `aligned_end_s` | float | **ALIGNER가 원본 타임라인에 복원한 구간** |
| `align_confidence` | float | 정렬 신뢰도(오디오지문/자막 매칭) |
| `views` / `likes` / `shares` / `comments` | int | 성과(ground truth 약신호) |
| `title` / `hook_text` | text | 사람이 쓴 훅(훅 모델 학습용) |

## Candidate — AI가 생성한 후보 구간

| 필드 | 타입 | 설명 |
|---|---|---|
| `candidate_id` / `source_id` | id | |
| `start_s` / `end_s` | float | 경계(문장/호흡/장면 스냅) |
| `variant_of` | id\|null | 같은 순간의 다른 in/out 변형 |
| `generator_version` | str | 어느 파이프라인이 만들었나(Experiment) |
| `features` | json | 멀티모달 신호 요약(평가자 입력) |
| `rationale` | text | 왜 골랐는지(LLM 설명, 디버깅·학습용) |

## Score — 평가자 점수 (3층, 평가자 버전별)

| 필드 | 타입 | 설명 |
|---|---|---|
| `score_id` / `candidate_id` | id | |
| `evaluator_version` | str | 어느 평가자가 매겼나 |
| `tier` | enum | heuristic / rubric_llm / proxy_reward / ground_truth |
| `value` | float | |
| `rubric_breakdown` | json | 훅 강도·자기완결성·payoff·감정아크 ... |

## Outcome — 실제 발행 성과 (Phase 4+)

| 필드 | 타입 | 설명 |
|---|---|---|
| `outcome_id` / `candidate_id` | id | |
| `views` / `avg_watch_pct` / `swipe_away_3s` / `shares` / `saves` / `follows` | num | 실지표 |
| `collected_at` | datetime | |

## Experiment — 버전 관리 (champion–challenger)

| 필드 | 타입 | 설명 |
|---|---|---|
| `experiment_id` | id | |
| `kind` | enum | prompt(L0) / pipeline(L1) / evaluator(L2) / policy(L3) |
| `config` | json | 프롬프트·가중치·하이퍼파라미터 등 전체 구성 |
| `parent` | id\|null | 어떤 챔피언에서 파생됐나 |
| `bench_metrics` | json | recall@k, IoU, boundary_err ... |
| `status` | enum | candidate / champion / retired |

## DecisionLog — 자기개선 연료

> 모든 모듈이 남기는 `(상태, 행동, 결과)` 트리플. 이게 곧 학습 데이터셋.

| 필드 | 타입 | 설명 |
|---|---|---|
| `log_id` | id | |
| `module` | str | collector/generator/evaluator/improver ... |
| `experiment_id` | id | 어느 버전이 한 결정인가 |
| `state` | json | 입력 상태(원본 요약·후보·이전 점수 등) |
| `action` | json | 취한 행동(선택·점수·파라미터 변경) |
| `result` | json | 결과(벤치 변화·성과·정답 일치 여부) |
| `ts_logical` | int | **논리적 순서(벽시계 아님 — 재현성 위해)** |
