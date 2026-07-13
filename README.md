# AI Shorts Editor — 자기개선형 쇼츠 추출 시스템

> "사람이 롱폼 영상을 보고 쇼츠를 뽑아내는 과정"을 그대로 재현하고, 나아가 사람보다 더 잘하는 것이 목표.
> 사람은 데이터의 일부만 제시하고, AI가 스스로 데이터를 찾고 · 파이프라인을 구성하고 · 결과를 분석해 다시 개선하는 **자기개선(self-improving) 루프**로 동작한다.

---

## 핵심 철학: 이건 "파이프라인"이 아니라 "데이터 플라이휠"이다

쇼츠 추출 파이프라인을 만드는 게 목표가 아니다. **스스로 좋아지는 루프**를 만드는 게 목표다.
파이프라인도, 파인튜닝도 그 루프 안의 부품일 뿐이다.

```
사람이 방향(채널/주제)만 제시
        │
        ▼
 ① AI가 원본 + 사람이 만든 클립 + 성과를 자율 수집  ──┐
        │                                              │
        ▼                                              │
 ② 멀티모달 이해 → 후보 구간 생성                       │
        │                                              │  데이터가
        ▼                                              │  쌓일수록
 ③ 평가자(3층 보상)로 랭킹                              │  모든 단계가
        │                                              │  좋아진다
        ▼                                              │  (compounding)
 ④ 사람 클립 대비 성능 측정(오프라인 벤치마크)          │
        │                                              │
        ▼                                              │
 ⑤ 결과 분석 → 프롬프트/파이프라인/평가자/모델 개선  ───┘
```

핵심 원칙 세 가지:
1. **파인튜닝은 첫 레버가 아니라 마지막 레버.** 싼 레버(프롬프트→파이프라인→평가자)부터 돌리고, 정체될 때만 올라간다.
2. **평가자(evaluator)가 키스톤.** 평가자가 나쁘면 모든 자기개선이 쓰레기를 향해 최적화된다. 자기개선 품질 = 평가자 품질.
3. **모델보다 루프를 먼저.** 모든 결정을 `(상태, 행동, 결과)` 로그로 남긴다. 이 데이터셋이 곧 해자(moat)다.

---

## 프로젝트 결정 (확정)

| 항목 | 결정 | 함의 |
|---|---|---|
| **콘텐츠 도메인** | 드라마 · 영화 · 예능 · 유튜브 롱폼 | 멀티모달(영상+오디오+자막) 필수. 예능 편집자막 = 공짜 라벨 |
| **"사람보다 잘함"의 측정** | 오프라인 벤치마크 우선 (사람 클립 대비 recall) | 발행 0번, ToS·저작권 리스크 최소, 반복 100배 빠름 |
| **모델·컴퓨트 전략** | 하이브리드 — 단계적 확장 | Phase 0~2 API 전용 → 평가자(L2)부터 자체 학습 → 정체 시 L3 파인튜닝 |
| **시드 데이터** | 아직 없음 (백지) | AI가 수집 전략부터 설계. 인터넷의 "원본↔클립" 쌍이 출발점 |

---

## 자기개선이 일어나는 4개 레벨 (싼 것부터)

| 레벨 | 자기개선 방식 | 비용 | 도입 시점 |
|---|---|---|---|
| **L0 프롬프트/룰** | 성과를 보고 자기 프롬프트·루브릭·임계값을 재작성 (reflection / DSPy류) | 최저 | Phase 2 |
| **L1 파이프라인** | 파이프라인 구성을 유전·밴딧·베이지안 탐색 ("AI가 스스로 파이프라인 구성") | 중 | Phase 2 |
| **L2 평가자(보상모델)** | 누적 데이터로 보상모델 학습·보정 — **최고 레버리지** | 중 | Phase 3 |
| **L3 정책 파인튜닝** | 컷 선택·훅 작성 모델을 winner/loser로 SFT→DPO→RL | 최고 | Phase 5 (정체 시) |

---

## 문서

- [docs/OVERVIEW.md](docs/OVERVIEW.md) — **★ 여기부터 읽으세요.** 처음 보는 사람을 위한 종합 정리(구조·단계별 로직·피처·현황·발견·용어집)
- [docs/INTEGRATION_PLAN.md](docs/INTEGRATION_PLAN.md) — **★ (7/13) 세 조각 통합 계획 v1.** ai-video·factory·자가개선을 이중 루프(예시 주입+config 개선)로 통합, +7D 창·+11D 채점 단일 리듬, A/B 판정 규칙(2방식·4중 관문), Phase 0 선행 수리 6건, 자동화 스케줄·대시보드
- [docs/M1_FINDINGS_AND_DIRECTION.md](docs/M1_FINDINGS_AND_DIRECTION.md) — **★최신 결론(6/16): 관측 천장 + 돌파구.** 관측 절대예측은 천장이나, ai-video 자기채널을 같은-작품 시장과 비교(상대 벤치마크)하니 시청유지 격차+원인(무음·라우드니스·길이·montage) 규명. directive + 코드 매핑.
- [docs/AB_VALIDATION.md](docs/AB_VALIDATION.md) — **★ 자기개선 루프 닫기: A/B 검증 설계.** directive(가설)를 우리 채널에서 인과 증명하는 설계(Exp1=라우드니스, 작품내 2-variant, 로깅 스키마, paired 분석). ai-video PR #15(loudness) 연동
- [docs/SELF_IMPROVEMENT_SPEC.md](docs/SELF_IMPROVEMENT_SPEC.md) — **(6/16 세션, 현행 권위 설계)** 신규 격리 Supabase(`fdidiqdhcyctdbogxkdu`) 위 리워드/평가 레이어 상세 설계. 0번 비판 + 4단계 흐름 + 피처 x + 보상 y(이중 잔차·VLM judge·캘리브레이션) + 리워드 모델 2단 루프 + 자가개선 + 마일스톤. GROUNDED_DESIGN 위에 리워드 레이어를 얹음
- [docs/schema.sql](docs/schema.sql) — **위 설계의 멱등 DDL** (information_schema 가드 마이그레이션)
- [docs/M0_ETL.md](docs/M0_ETL.md) — **M0: laeebly→격리 ETL 설계** + 소스 실측(60,695영상·+14일 36k) + M1 게이트. 적재: [scripts/etl_laeebly_to_pipeline.py](scripts/etl_laeebly_to_pipeline.py)
- [docs/M1_FEATURE_EXTRACTION.md](docs/M1_FEATURE_EXTRACTION.md) — **M1: 피처 x 추출 어댑터**(ai-video 모듈 재사용 매핑) + feature_registry 시드 + moat 검증 게이트
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 시스템 구성, 멀티모달 신호, 4레벨 자기개선
- [docs/DATA_SCHEMA.md](docs/DATA_SCHEMA.md) — `(상태, 행동, 결과)` 로그 데이터 모델 (플라이휠의 토대)
- [docs/EVALUATION.md](docs/EVALUATION.md) — recall 벤치마크 정의, 3층 보상, 클립↔원본 자동 정렬
- [docs/DATA_COLLECTION.md](docs/DATA_COLLECTION.md) — 자율 수집 전략, 저작권/ToS 원칙
- [docs/ROADMAP.md](docs/ROADMAP.md) — Phase 0~5 구체 태스크와 완료 기준
- [docs/GROUNDED_DESIGN.md](docs/GROUNDED_DESIGN.md) — **(6/14 세션)** `ai-video` 실코드 · laeebly 실데이터 · 단일 채널 닫힌 루프로 고정한 실행 설계. 위 문서들의 전제 3가지(그린필드 GENERATOR / 시드 없음 / 발행 0번)를 갱신

---

## 윤리 · 법적 원칙 (요약)

- **오프라인 학습·평가 한정.** 수집한 드라마/영화/예능 영상은 자동 재발행하지 않는다.
- **영상 원본은 영구저장·재배포 금지.** 타임코드 + 추출 피처(임베딩, 자막 텍스트, 점수)만 영구 보관.
- **공식 API 우선.** 스크래핑은 각 플랫폼 ToS 범위 안에서. rate-limit 준수.
- **보상 해킹 경계.** 프록시 지표(낚시성 훅 등)만 올리고 실가치를 해치지 않도록 ground truth를 항상 루프에 유지.
