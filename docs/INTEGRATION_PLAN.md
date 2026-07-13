# 통합 계획 — 세 조각을 하나의 시스템으로 (v1)

> **2026-07-13 세션 확정.** 대상: ① ai-video(생성) · ② factory 분석공장+예시뱅크(`eb_*`) · ③ 자가개선(루트 `scripts/`).
> 목표: 세 조각을 **이중 루프**(빠른=예시 주입 / 느린=config 개선)로 묶고, **+7D 창·+11D 채점**의
> 단일 리듬으로 반자동 운행한다. 사람 개입은 2지점(발행 공개 전환 · 승격 승인)만 남긴다.
> 검증 근거: 이 문서의 file:line 은 2026-07-13 기준 코드 실측(9-에이전트 정독·비판 패스).

---

## 0. 확정 결정 (이 문서의 뼈대)

| # | 결정 | 근거 |
|---|---|---|
| **D1** | **순차 before/after 비교 기각.** 검증은 (i) 코호트 벤치마크(기본) (ii) 동일 edit_plan 쌍 A/B(렌더 노브 전용) 2방식만 | 시간 교란·중복 콘텐츠 억제·n=1 검정 불가. 인터리브 발행 전제([AB_VALIDATION.md:73](AB_VALIDATION.md)) |
| **D2** | **판정 창 통일: +7D 창·+11D 채점**(커버리지 게이트) — factory와 동일 리듬. **+14d는 판정→자동 감사로 강등** | [SELF_IMPROVEMENT_SPEC.md](SELF_IMPROVEMENT_SPEC.md) §0-4(조기속도가 편집 귀인에 더 깨끗) · ETL `WINDOWS=[1,3,7,14]` 이미 적재([etl_laeebly_to_pipeline.py:25](../scripts/etl_laeebly_to_pipeline.py)). 반대신호는 [OVERVIEW.md:149](OVERVIEW.md) 핵심발견 8뿐(1줄 요약, 상세분석 부재) → 감사로 헤지 |
| **D3** | **지표 역할 고정** — eb 복합점수=예시 인출 라벨 전용 / judge=안전게이트 전용 / **승격 1차 지표 = +7d apv 시장 백분위(margin 0.03)** | 지표 삼중화 방지. "관측 격차→규칙 직행 금지"([pipeline_three_frames_v0.1.md:294](../factory/pipeline_three_frames_v0.1.md)) |
| **D4** | **주입은 검증 대상 노브** `injection{off,on}` — 기본 ON이 아니라 A/B로 증명 후 채택 | 주입 효과는 미검증 가설. SPEC §5-6(RAG few-shot)의 선례를 노브로 편입 |
| **D5** | **사람 개입 2지점**: ① 발행 공개 전환(자동 private 업로드까지는 기계) ② 승격 승인(approve-to-proceed) | 오채널 사고·소표본 노이즈의 프로덕션 확정 방지 |
| **D6** | 판정 지표는 **root apv**(watch_time_hours 산출) 백분위 유지 — 창만 +7D로 통일 | factory `kept_watching_rate`는 2026-06-19 이전 ~93% 결측(웨이브2), apv는 전 구간 조밀 |

---

## 1. 목표 아키텍처 (이중 루프)

```
        ┌────────────────── 빠른 루프 (매일: 뱅크가 두꺼워짐 → 다음 생성의 예시) ─────────────┐
        │                                                                                    │
        ▼                                                                                    │
┌───────────────┐     ┌───────────────┐     ┌───────────────┐     ┌───────────────┐     ┌────┴──────────┐
│ ① ai-video    │ ──▶ │ 발행           │ ──▶ │ laeebly       │ ──▶ │ ② 분석 공장    │ ──▶ │ ③ 예시뱅크     │
│ config 노브    │     │ 자동 private   │     │ 일별 성과 수집 │     │ +11d·+7D 창   │     │ eb_* (시장만   │
│ + 예시 주입    │     │ → 사람 공개    │     │ (적재 ~4d 지연)│     │ 게이트·2트랙   │     │  인출)        │
└───────┬───────┘     └───────────────┘     └──────┬────────┘     └───────────────┘     └───────────────┘
        ▲                                          │
        │                                          ▼ (+7d 커버리지 게이트, 같은 날)
        │             ┌───────────────┐     ┌───────────────┐
        └──────────── │ 다음 config    │ ◀── │ 판정           │ ◀── 측정: +7d apv 시장 백분위
   느린 루프           │ 1-노브 변경    │     │ margin 0.03   │     (frozen comparator, 자사 제외)
   (~11일/라운드)      │ 사람 승인      │     │ 승격/폐기      │ ──▶ D+18: +14d 자동 감사(역전 시 경보·롤백)
                      └───────────────┘     └───────────────┘

관측: 대시보드 — 체인 단계별 재고·체류시간 · ETL 지연 경보 · 라운드 보드 · 백분위 추이 · Goodhart 카나리아
```

## 2. 한 라운드 타임라인

| 시점 | 일어나는 일 | 주체 |
|---|---|---|
| **D0** | loop_controller가 라운드 config 제안 → 코호트(여러 작품×여러 편) 생성. 주입 ON이면 뱅크에서 예시 카드 인출(스냅샷을 provenance에 고정) → 자동 private 업로드 → **사람: 공개 클릭** | 기계+사람① |
| **D1~10** | laeebly 일별 성과 축적(적재 ~4d 지연). autoloop가 신규 run 자동 평가(안전게이트: 환각·깨짐) | 기계 |
| **D+11** | **커버리지 게이트**(`max(upload_at) ≥ publish+7d`) 통과 시, 같은 날: (a) factory가 코호트를 eb에 적재·채점 (b) 판정 — 코호트 각 편의 +7d apv를 같은 작품 시장 대비 백분위화 → 평균이 baseline+0.03 초과면 채택 → **사람: 승격 승인** | 기계+사람② |
| **D+18** | **+14d 감사**: 같은 판정을 +14d 창으로 자동 재계산. 역전 시 경보+롤백 제안. 초기 3~4라운드 일치율 실측 후 감사를 샘플링으로 축소 | 기계 |

---

## 3. 선행 수리 (Phase 0 — 루프 가동 전 필수)

### 3-1. ip_key/모집단 통일 (P0) — 4단계 순서 고정

현상: [scoring.py:75](../factory/scoring.py)가 `identification_code or licensed_video_title`로 "같은 원작" 모집단을
그룹핑 → 코드가 null인 자사 채널 클립은 **원작 1단 모집단 자체가 없어** 클러스터/전체 단으로 추락.
같은 원작의 시장 클립과 작품 내 상대평가 원천 불가. eb_ip도 코드-키/`t:`-키 2행으로 분열
([cluster.py:297,300](../factory/cluster.py) — 제목 역매핑 부재).

1. [db.py:227-228](../factory/db.py) lateral SELECT에 `v.identification_code` 회수 추가 + [cluster.py:249](../factory/cluster.py)에서 폴백 사용 — 제목으로만 연결된 클립 즉시 라이선스 경로 복구 (1줄급)
2. `IPRegistry.__init__`에 `_norm_title` 역인덱스(코드-키 행의 title 기준) → `_finalize_unlicensed`에서 히트 시 코드-키 행 상속, `t:` 행 생성 억제
3. `eb_shorts_features.ip_key` 컬럼 추가 + `resolve()` 반환 확장 + [run_factory.py:218-222](../factory/run_factory.py) 저장 + [scoring.py:75](../factory/scoring.py)를 `ip_key` 우선으로
4. 기존 `t:` 행 재키잉 + `--score-mode mutual` 재채점 1회 (①②만으론 모집단이 계속 갈라짐 — scoring은 eb_ip를 안 읽음)

### 3-2. 에코챔버 차단 (P0)

자사 채널 클립이 factory 게이트(채널 필터 없음)를 무표식 통과해 뱅크 유입 →
[make_report.py:71-95](../factory/make_report.py) `retrieve()`에 출처 필터가 없어 자기 산출물 재주입 가능.

- `eb_shorts_features.origin text ('market'|'ours')` 컬럼 + factory config에 자사 채널 ID 상수 → 적재 시 세팅, 기존분 UPDATE 백필
- 인출 모집단·글로벌 폴백·탐색 슬롯·lift 분모(채널 기저)·시장 비교군 전부에서 `ours` 하드 제외
- 골든 홀드아웃 클러스터 1~2개 지정(주입 없음) — 뱅크 오염·mode collapse 카나리아

### 3-3. 비교군 오염 차단 (P0)

[m3_aivideo_benchmark.py](../scripts/m3_aivideo_benchmark.py)의 `AIV_IDS`(2026-06-16 하드코딩 스냅샷)를
`SELECT video_external_id FROM clips WHERE source='auto_edit'` 동적 조회로 교체. 루프가 돌수록 신규
자사 클립이 "시장"에 섞이는 것을 차단.

### 3-4. 생성 경로 설정 통일 (P0)

[autogen.py:30-43](../scripts/autogen.py) `build_gen_cmd`가 GOOD_FLAGS/라운드 config를 안 붙임 —
gen_queue 경로와 [generate_batch.py](../scripts/generate_batch.py) 경로의 생성 설정 불일치. 자동 재생성분이
실험 config와 다른 설정으로 만들어지면 A/B 자체가 오염되므로 라운드 config 주입을 두 경로에 통일.

### 3-5. 오채널 업로드 차단 (P1)

[publish_youtube.py:36-39](../scripts/publish_youtube.py) 미등록 채널명 → generic `YT_REFRESH_TOKEN`
폴백 제거, 하드 실패로 변경.

### 3-6. 판정 창 전환 (P0, D2 구현)

- [m3_aivideo_benchmark.py:48,68](../scripts/m3_aivideo_benchmark.py) · [decide_experiment.py:48](../scripts/decide_experiment.py) · [m4_ab_analysis.py:47](../scripts/m4_ab_analysis.py) · [loop_controller.py cohort_percentile](../scripts/loop_controller.py)의 `snapshot_window_days=14` 하드코딩 → 파라미터화(기본 7, 감사 패스 14)
- **baseline 재산출**: `loop_state`의 baseline 0.21은 +14d apv 기준 — +7d 기준으로 1회 재계산(안 하면 첫 라운드부터 기준선 불일치)
- 판정·감사 트리거는 벽시계가 아니라 **커버리지 게이트**(factory [GATE_SQL](../factory/db.py) 패턴을 +7d/+14d에 이식)

---

## 4. A/B 판정 규칙 (채택/폐기)

### 4-1. 두 방식의 분업

| | 방식 ① 쌍 A/B (엄밀) | 방식 ② 코호트 벤치마크 (기본) |
|---|---|---|
| 언제 | 화면이 동일한 **렌더 노브** (라우드니스) | 콘텐츠가 달라지는 노브 (무음·길이·주입) |
| 단위 | 동일 edit_plan 재렌더 쌍 (treatment/control) | config 1개 × 코호트 N편 |
| 발행 | 같은 채널·같은 시기·순서 무작위 교대 | 시기 자유 (백분위가 시기 운 상쇄) |
| 측정 | 쌍별 Δapv(+7d) | 편별 같은-작품 시장 백분위(+7d, frozen comparator)의 코호트 평균 |
| 채택 | 평균 Δ>0 **AND** 부호검정 p<0.05 ([m4_ab_analysis.py:75-77](../scripts/m4_ab_analysis.py)) | 코호트 백분위 − baseline > **0.03** ([decide_experiment.py:66](../scripts/decide_experiment.py)) |
| 표본 | 5쌍 전승 = 최소 유의 · 0.3σ 검출엔 30~40쌍 | 라운드당 다작품 코호트 |

쌍 무결성은 3겹으로 강제: [register_ab_experiment.py:24-37](../scripts/register_ab_experiment.py) validate_pair →
DB 트리거 R1~R4([0001_ab_pair_invariants.sql](migrations/0001_ab_pair_invariants.sql): arm enum·서로 다른 영상·같은 작품·쌍당 arm 1행) →
loop_controller provenance 검증([loop_controller.py:119-125](../scripts/loop_controller.py): 자사 생성물 아니면 등록 거부).

### 4-2. 채택의 4중 관문

1. **margin/유의성** (위 표)
2. **가드레일 자동화**: apv는 짧을수록 유리한 정규화 artifact → 절대 시청시간·likes/shares를 m4에 자동 계산 추가(현재 문서상 수동 — [AB_VALIDATION.md:37-40](AB_VALIDATION.md)). 특히 길이 노브 실험은 가드레일 없이 판정 금지
3. **사람 승격 승인** (D5)
4. **+14d 감사** (D2)

### 4-3. 신규 불변식 R5 (추가)

쌍 등록 시 `|published_at(T) − published_at(C)| ≤ 48h` 검증 — "동시 인터리브 발행"이 현재 문서 규율뿐이므로
DB/앱 레벨로 강제. before/after가 몰래 쌍으로 등록되는 것을 기계적으로 차단.

---

## 5. 예시 주입 v0 (빠른 루프의 신규 단계)

배포는 검증된 [APPLY.md](../integration/ai_video/APPLY.md) 패턴 재사용: `integration/ai_video/injection.py`
1파일 + ai-video pipeline.py에 가산적 2줄. 인출·조립 로직은 [make_report.py](../factory/make_report.py) 목업 이식.

- **v0 구성 = 4조각 중 2조각**: ① 참조 카드 3장(시장 클립 · `score_basis='full'` 우선 · `[:70]`/`[:30]` 잘림 전부 제거) + ③ 탐색 슬롯 1장(**클러스터 밖**에서 — 목업의 클러스터 내 선택은 발산 목적과 불일치, [make_report.py:86](../factory/make_report.py)). ② 대조쌍은 클러스터에 good·bad full 라벨 각 2+ 있을 때만. ④ dossier는 **섹션째 생략**(placeholder 텍스트 주입 금지)
- **provenance 규약 (전제조건)**: 주입 정책(cluster, k, 필터, score_version)은 AppConfig 필드로 → config_hash 자동 반영([ingest_aivideo_run.py:52-58](../scripts/ingest_aivideo_run.py)). 실제 인출 스냅샷(shorts_id 목록·뱅크 as-of 시각)은 run_log 별도 키로 스탬프(config에 넣으면 run마다 hash가 유일해져 "같은 레시피" 그룹핑 붕괴). **이 스탬프 없는 주입 배선 금지**
- **첫 검증**: `injection{off,on}`을 loop_controller 노브로 등록, ON/OFF 코호트 +7d 백분위 비교. 기존 3노브 라운드와 분리
- **표절 방어 3중**: few-shot 블록을 TASK와 물리 분리 + 카드 단위 "기능 기술 — 문구 복사 금지" 반복 + 생성물↔참조 note n-gram 중복 시 재생성. 카드당 ~500토큰 상한
- **작품→cluster resolver**: 라이선스 작품은 identification_code 경로 강제(제목 exact 매칭은 침묵 실패→주입 스킵+provenance 기록). 신규 작품은 원작 메타로 `make_cluster_id` 직접 계산(enum 정합 테스트 포함)

---

## 6. 자동화 — 스케줄 4잡 + 상태 이관

현재 repo에 스케줄 등록물 0건(전 구간 사람 트리거). "하나의 시스템"의 첫 단추는 잡 4개 커밋:

| 잡 | 주기 | 트리거 |
|---|---|---|
| factory 적재 (`run_factory --limit N`) | 일 1회 | laeebly ETL 완료 추정 시각 후. 멱등 |
| autoloop 평가 (플래그 없이) | 3~6h | run 발견→인제스트→피처→안전게이트. 멱등 |
| 측정·판정 폴러 | 일 1회 | **커버리지 게이트**: published 라운드의 코호트 전체가 +7d 통과 시 measure→decide→알림. 승격 시 +14d 감사 잡 자동 예약 |
| ETL 신선도 감시 | 일 1회 | `max(created_at)` 지연 5일 초과 시 경보 — 없으면 전체 체인이 "조용한 0건"으로 퇴화 |

- `loop_state.json`(로컬 파일 = 단일 장애점) → pipeline DB `loop_rounds` 테이블 이관, JSON은 export로 강등
- run 발견을 파일시스템 glob → DB 쿼리(`gen_queue done ∧ clip_metadata 미존재`)로 전환 — outputs 유실이 "조용한 누락"이 아니라 감지 가능한 에러가 되도록
- cron 겹침 대비 DB advisory lock
- 승격 반영은 approve-to-proceed: 판정 결과를 "제안 config diff + 근거 수치" 알림으로, 사람 승인 시 다음 라운드 propose

## 7. 대시보드

씨앗: [status_report.py](../scripts/status_report.py)의 `q()/one()`을 dict 반환으로 분리 → JSON 모드.
1단계는 cron 재생성 정적 HTML(레포 기조와 일치), 필요 시 Streamlit 승격. 통합 뷰
`v_loop_trace`(clip_metadata → clips.video_external_id → eb_shorts_features.shorts_id soft join) 신설.

핵심은 집계가 아니라 **침묵 감지**:
1. 체인 단계별 재고·최고 체류시간 (gen_queue pending → 미처리 run → 미발행 PASS → 미연결 발행분 → +7d 대기 → 판정 완료)
2. ETL 지연 경보 (laeebly `max(created_at)` lag)
3. 라운드 보드 (상태·경과일·baseline 대비 백분위 추이)
4. 뱅크 상태 (eb 적재 수 origin별 · 클러스터 커버리지 · "발행+8d 경과했는데 eb 미적재" = 공장 밀림 신호)
5. Goodhart 카나리아 (주입 예시 복합점수 분포 vs 생성물 +7d 백분위 상관 — 점수↑ 백분위→ 면 주입 규칙 동결)

---

## 8. 실행 순서

| Phase | 내용 | 완료 기준 |
|---|---|---|
| **0** | §3 선행 수리 6건 (코드만으로 완결) | 자사 클립이 같은-원작 모집단에서 채점됨 · 인출/비교군에서 ours 제외 · 판정 스크립트 +7d 동작 · baseline +7d 재산출 |
| **1** | §6 스케줄 4잡 + 상태 DB 이관 + factory 백로그 적재 계속 | 사람 개입 없이 1주일 무인 운행(생성 제외) · ETL 경보 동작 확인 |
| **2** | §5 주입 v0 + provenance 스탬프 → `injection{off,on}` 라운드 | ON/OFF 코호트 판정 1회 완료(채택이든 폐기든) |
| **3** | 느린 루프 정규 운행 (1노브/라운드) + 작품 축 병렬 + +7d 조기 프루닝 | 라운드 2회 연속 자동 판정·감사 완료 |
| **4** | §7 대시보드 | 5개 지표 한 화면 · 침묵(0건 퇴화) 1회 이상 실검출 |

## 9. 미해결 / 후속

- **+7d↔+14d 판정 일치율 실측** (초기 3~4라운드) — 일치율 높으면 감사 샘플링 축소, 역전 잦으면 +14d 판정 복귀 (루프가 스스로 답하게 함)
- OVERVIEW 핵심발견 8("+14일에서 상관 또렷")의 원 분석 재현·기록 — 현재 1줄 요약뿐
- `eb_channels.dossier` 생성 패스 (주입 ④조각) — 주입 A/B 양성 이후의 후속 노브
- 결정론 추출기 이원화(root [feature_extractor.py](../extract/feature_extractor.py) vs [factory/extract.py](../factory/extract.py)) — 산식 계약(파라미터·정의) 공용 상수로 통일, 버전 문자열에 계약 버전 명기
- 문서 역반영: ROADMAP/OVERVIEW에 예시뱅크·이중 루프·+7D 리듬 반영 (현재 factory 문서에만 존재), factory 문서의 "9버킷" → 10버킷 정정
