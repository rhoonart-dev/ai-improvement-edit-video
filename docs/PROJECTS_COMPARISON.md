# 두 프로젝트 비교 & 합치기 계획 (2026-06-19)

> 대상: **ai-improvement-edit-video**(이 저장소, "improvement") ↔ **ai-improve-edit-video**(형제, "improve").
> 둘 다 같은 목표 — ai-video(라이선스 롱폼→쇼츠 자동편집)의 **평가/리워드 레이어(moat)를 데이터로 자가개선**.

## 0. 한 줄
**둘은 같은 프로젝트의 분기(fork)다.** `M1_FINDINGS_AND_DIRECTION.md`가 거의 동일(관측 천장 3중 확정·벤치마크 돌파·같은 수치) — 같은 분석/결론에서 출발해 구현이 갈렸다.
- **improvement = 두뇌**: 증거·신뢰성·올바른 리워드 신호(벤치마크)에 집중, 보수적.
- **improve = 손발**: 자동화·발행·프로비넌스 풀 배관, 공격적.
→ 상호보완적이나 **따로 굴리면** 양쪽 단점만 실현(형제는 나쁜 신호로 자동발행, 이쪽은 옳은데 못 굴림).

## 1. 비교
| 축 | improvement (이쪽) | improve (형제) |
|---|---|---|
| 공통 출발 | M1 분석(천장·벤치마크) 동일 · `m1_moat_gate`/`m1_within_work_pairs`/`m3_aivideo_benchmark`/`m2_pairwise` 공유 | (동일) |
| 리워드 신호 | **벤치마크 백분위**(시장 대비) — 증거(judge 불신)와 일관 | **LLM judge 점수**(`judge_runs`) + recall, 콜드스타트 예측 |
| 실험/루프 | paired A/B(`register_ab_experiment`·`m4_ab_analysis`) + coordinate-ascent `loop_controller`(수동) | 챔피언-챌린저 승격(`decide_experiment --metric judge`) + **`autoloop`(완전 자동)** |
| 발행/프로비넌스 | **없음**(루프=JSON 장부) | `publish_youtube`·`ingest_aivideo_run`·`link_published`·`reconcile_published`·`build_provenance`·`status_report` |
| ai-video 수정 | loudness #15/16, silence/length #18 **머지**(공유 repo) | (동일 — ai-video는 공유) |
| 성향 | 보수적(학습형 리워드 보류, 자동발행 경고) | 공격적(자동 생성+발행까지) |

**improvement 강점:** 증거-일관 신호 · paired A/B + 루프정책 · 단위테스트 · 보수적 게이팅.
**improvement 약점:** 비교군 오염 버그 · 루프가 임의 ID 신뢰 · 발행/링크/프로비넌스 전무 · 미커밋.

**improve 강점:** 완전 운영 루프(autoloop) · 발행→content_id 정합 · 프로비넌스 동기화 · 배관 테스트.
**improve 약점:** **자기 증거가 부정한 judge 신호 위에 자동화**(빠른 루프 게이팅 `evaluate_run --quality-min`, 승격 `--metric judge`) · 자동 발행 위험(소형채널 ToS/스팸) · 승격이 오프라인 judge 마진(검증된 랜덤 A/B 아님).

## 2. 둘 다의 문제 (공통)
1. **비교군 오염**(Codex #1) — `m3_aivideo_benchmark.load_work_others`가 현재 코호트만 제외 → 새 코호트 측정 시 옛 ai-video 클립이 시장 비교군에 남아 백분위 가짜 상승. **[정밀화/수정 2026-06-19]** 실제로 *살아있던 건 improvement* (m3를 `--ids-file` 새 코호트로 일반화 + `loop_controller`에 연결). → `comparator_exclude(cohort)=AIV_IDS ∪ cohort` 로 **수정 완료**(frozen comparator, 42 tests, 구 코호트 21% 불변). 형제는 m3가 AIV_IDS 하드코딩(새 코호트 경로 없음)이라 미발현 + 루프가 judge 기반 → **머지로 cohort-벤치마크 승격을 도입할 때 이 수정본을 반드시 이식**.
2. **약한 인과 근거로 승격** — 검증된 **랜덤 A/B 부재**. 백분위(이쪽)·judge(형제) 모두 비인과 오프라인 신호.
3. **judge 모순이 프로젝트 전반** — 두 증거(judge pairwise 위치편향·절대점수 포화)와 달리 공유 SPEC은 "VLM judge=moat"를 깔고, 형제는 그 위에 루프를 올림.
4. **A/B 쌍 불변식 미강제**(Codex #3) — 같은 edit_plan·채널·랜덤화·source_work를 DB 제약 아닌 operator/CSV 신뢰.
5. **운영 천장 공유** — 소형 채널(~7만뷰)+생성 머신 한계 → 어떤 루프든 데이터 검정력 약함.
6. **★메타: 같은 프로젝트 2개 분기 유지** — 중복 유지보수 + 모순(judge) 누적.

## 3. 합치기 계획
**목표 = 한 저장소.** 형제의 **배관/자동화**(재구축 비쌈)를 베이스로, 이쪽의 **신호·A/B·게이팅**을 이식.

**(A) 베이스 = 형제(improve)** — autoloop·publish·link·reconcile·provenance·status 유지.

**(B) improvement → 형제로 이식할 것**
- `m3_aivideo_benchmark`(비교군 버그 수정본) + 코호트 일반화(`--ids-file`)
- paired A/B: `register_ab_experiment` + `m4_ab_analysis` + **DB 불변식**(edit_plan/channel/randomize/source_work)
- `loop_controller`(coordinate-ascent) — 단 ID 대신 **provenance 바인딩**(형제 link/ingest 위에서)
- **게이팅 철학**: judge는 *빠른 루프 프록시*로만, **승격·최종은 벤치마크 백분위 + +14일 실측 + paired A/B**

**(C) 합치기 전 선결(둘 다 깨진 것 먼저)**
1. 비교군에서 *모든* ai-video 산출물 제외(frozen comparator) — `clips.source='existing'` + 채널 제외.
2. 루프가 `ids_file` 대신 **생성-run provenance**에 바인딩(형제 `ingest_aivideo_run`/`link_published` 재사용).
3. A/B 쌍 불변식 **DB 제약**으로 강제(잘못된/교차채널·교차스토리 쌍 불가).
4. **judge 모순 정리**: 두 SPEC에서 "judge=moat" 가정을 증거에 맞게 수정, 형제 judge-게이팅을 벤치마크/실측으로 교체 검토.

**(D) 순서**: 선결버그(C1~3) → 베이스 확정(A) → 신호/A-B 이식(B) → judge 역할 재정의(C4) → 단일 저장소 운영.

## 4. 핵심 메시지
> improvement(올바른 신호·증거) + improve(자동화·배관) = 완성. 분리 유지하면 **형제가 부정된 신호로 자동발행**하는 최악을 굴린다. 합치되, **공통 버그(비교군·프로비넌스·A/B 불변식)와 judge 모순을 먼저** 해소.

---
*근거: 양 저장소 `docs/M1_FINDINGS_AND_DIRECTION.md`(동일), 형제 `scripts/{autoloop,publish_youtube,decide_experiment,evaluate_run,link_published,ingest_aivideo_run}.py`, 이쪽 `scripts/{m3_aivideo_benchmark,m4_ab_analysis,register_ab_experiment,loop_controller}.py`, Codex adversarial-review(2026-06-19).*
