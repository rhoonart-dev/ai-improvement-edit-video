# M0 — 데이터 토대 & ETL (laeebly → 격리 파이프라인)

> 설계 근거 [SELF_IMPROVEMENT_SPEC.md](SELF_IMPROVEMENT_SPEC.md). 대상 격리 프로젝트
> `fdidiqdhcyctdbogxkdu`. 소스 laeebly `youtube_studio`(prod, **읽기 전용**).

## 확정 답변 (2026-06-16)
- `content_id` = 유튜브 영상 고유 id (채널 발행 영상 **전체**, 쇼츠+롱폼 혼재).
- **x(편집 피처)는 영상 파일을 직접 분석해 추출** — youtube_studio엔 성과 y만 있음.
- laeebly **읽기 전용** 쿼리 허용(쓰기·PII·매출 제외).

## laeebly 소스 실측 (집계, 2026-06-16)
| 항목 | 값 |
|---|---|
| 행 / 영상 / 채널 / 작품 | 4,623,972 / 60,695 / 233 / 204 |
| Shorts ≤60s | 51,713 (85%) |
| 60–180s / >180s / 길이 NULL | 4,113 / 3,894 / 975 |
| 작품(`licensed_video_title`) 보유 영상 | 20,474 (34%) |
| 수집 개시(최초 stat date) | 2025-04-10 |
| 발행 ≥2025-05 / **+14일 가능** | 45,098 / **36,021** |
| 영상당 평균 일별 행 | 76.2 |

→ **핵심**: 85%가 진짜 Shorts, **+14일 윈도우 확보 영상 36k** → M1 부트스트랩 표본 충분.
작품은 34%만 매핑 → 작품 정규화는 부분집합, 채널 정규화는 전체.

## 분석 단위 & 범위
- 리워드 모델링 1차 대상 = **Shorts(`video_length` ≤ 180s)**. 롱폼은 컨텍스트/원본 취급.
- `clips.is_format_short = (len ≤ 180)`; 캐논 Shorts는 ≤60s.

## 스키마 정렬 (M0에서 적용 완료)
- `clips.source`에 `'existing'`(중립) 추가 — 전체 분포 적재(극단 good/bad만 X). ✅ migration
- `clips.work_id` **NULL 허용** — 작품 미상 66% 수용, `v_training_matrix`는 `LEFT JOIN works`. ✅ migration
- good/bad는 **저장하지 않고 파생 라벨**(채널내 성과 백분위)로 — 골든시드/파인튜닝쌍 선별 시 계산.

## 소스 → 타깃 매핑
| 타깃 | 소스(youtube_studio) | 변환 |
|---|---|---|
| `channels` | channel_id, channel_name, subscribers | `DISTINCT ON (channel_id)` 최신행; platform='youtube' |
| `works` | licensed_video_title (NOT NULL) | DISTINCT title |
| `clips` | content_id, channel_id, licensed_video_title, video_length, publish_time | content_id별 집계; source='existing'; is_format_short=len≤180; work backfill(없으면 NULL) |
| `clip_performance` | views, watch_time_hours, impressions, impression_click_rate (일별) | +{1,3,7,14}일 윈도우 SUM(아래) |
| `clip_metadata` | — | auto_edit(우리 산출물)만; 기존 영상은 비움 |

**제외(복사 금지)**: `email`(PII), `profits_krw`·`*_revenue`·`shopping_*` 등 매출 컬럼.

**데이터 품질(검증 적재서 발견 — 스크립트에 반영):**
- `content_id`에 선행/후행 공백 존재 → `btrim` 후 식별·조인. `licensed_video_title=''`(빈 문자열) 제외.
- `subscribers` 컬럼이 대부분 0 → 채널 규모(통제 피처)로 불충분. 정규화용 채널 규모는 추후 YouTube Data API 보강 필요.
- 테스트성 채널(`channel_external_id` = `test`·`naver/kakao`) 혼재 → 분석 시 필터 고려.

## +N일 윈도우 산식 (일별 증분 → 누적)
youtube_studio는 **(content_id × 일)** 증분. `days_since = upload_at::date − publish_time::date`.
```
views_Nd     = Σ views                 where 0 ≤ days_since ≤ N
watch_Nd(h)  = Σ watch_time_hours      (동일 윈도우)
avg_view_pct = watch_Nd*3600 / views_Nd / len_sec        (= APV%)
ctr          = avg(impression_click_rate) → Shorts는 NULL (swipe 기반)
impressions  = Σ impressions
```
커버리지 부족(윈도우 내 일자 < 0.7·N) 영상은 해당 윈도우 스킵. **+14일 = y 확정 스냅샷**,
+1/3/7 = 조기속도 보조. 수집 개시 이전 발행 영상은 +14일 윈도우 부재 → **36k가 1차 표본**.

## x 피처 파이프라인 (영상 직접 분석) — M0 → M1
youtube_studio엔 x 없음 → 영상을 직접 분석한다.
1. **표본 추출**: Shorts ∩ +14일가능 에서 **장르×성과사분위 층화 2–5k**(전수 60k는 비용 과다).
2. **영상 확보**: content_id로 소스 영상 취득(분석 한정·재발행 금지 — ToS).
3. **ai-video 분석 모듈 재사용** → B/D/E(결정론적) + C(Gemini 2.5 Pro scene-obs) → `clip_features`.
4. **feature_registry 시드**(피처 메타·controllable·control_surface).

## M1 — moat 검증 게이트 (go/no-go)
- 타깃 = +14일 성과(잔차). baseline `B` = 컨텍스트(채널·작품 EB·발행시기·길이)만.
- 검정: **edit 피처 x가 B 너머 설명력을 더하는가** — nested CV ΔR²(회귀) / ΔAUC(good-bad), LRT/순열검정.
- **통과** = 유의·비자명 uplift → 전제 지지, M2/M3 진행. **≈0** → 전제 재고.

## 실행
[scripts/etl_laeebly_to_pipeline.py](../scripts/etl_laeebly_to_pipeline.py) — 멱등 적재(dim + 성과).
환경변수 `LAEEBLY_DB_URL`(읽기) · `PIPELINE_DB_URL`(쓰기). x 추출은 별도(위 4단계).

## 적재 완료 (2026-06-16)
✅ 풀 적재: clips **60,376**(shorts 55,536 · 작품매핑 20,378) / +14d 성과 **28,982** / +1·3·7d 합쳐 124,045행 · mean APV@14d 0.432.
연결: laeebly=direct(5432), pipeline=**aws-1 session pooler**(신규 프로젝트 direct는 IPv6 전용·미해석).

## 다음
1. ~~dim+성과 적재~~ ✅ 완료. 2. x 표본 추출([scripts/run_feature_extraction.py](../scripts/run_feature_extraction.py)) → `clip_features`. 3. baseline B + M1 게이트.
