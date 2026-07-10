# 쇼츠 분석 공장 v0.1

laeebly(원천, 읽기전용)에서 **+7D 성과가 온전한 쇼츠**만 골라, 영상을 Storage에 아카이브하고
2트랙(결정론+Gemini VLM) 피처를 추출한 뒤, +7D 성과·통제와 함께 `eb_*` 테이블
(video-improvement-pipeline)에 적재한다. 마지막에 채널 L1 갱신 + 성과 점수/라벨 재채점.

- 테이블 DDL: [eb_example_bank_tables_v0.1.sql](eb_example_bank_tables_v0.1.sql)
- 설계 문서: [pipeline_three_frames_v0.1.md](pipeline_three_frames_v0.1.md)
- **ai-video 담당자 참고**: [ai_video_injection_reference.md](ai_video_injection_reference.md) — 예시뱅크가 생성 쪽에 넘길 프롬프트 형태(주입 4조각) 목업 + seam 위치

> **★ 독립 모듈**: factory는 외부 폴더(t3_extract 등)를 참조하지 않는다. 추출 로직·xlsx·env
> 전부 factory 안에 있어, 통째로 ai-video / ai-improvement-edit 등에 얹을 수 있다.

## 구성 (전부 factory 내부)

| 파일 | 역할 |
|---|---|
| `run_factory.py` | 오케스트레이터 CLI (스테이지 실행 + eb_factory_runs/items 로깅) |
| `db.py` | Laeebly(psycopg2 직결, **읽기전용 세션 강제**) + Pipeline(PostgREST/Storage) |
| `extract.py` | 2트랙 피처 추출 (결정론 ffmpeg/scene/cv2 + Gemini VLM 10버킷) |
| `cluster.py` | 원작(IP) 레지스트리(eb_ip) — 라이선스=메타분류 / 비라이선스=desc+영상 원작식별 + 정규화키 |
| `backfill_clusters.py` | cluster 없는 쇼츠 재분류(저장 desc+로컬영상) — 소급 채우기 |
| `scoring.py` | lift(채널기저) → 캐스케이드 백분위(as-of) → 복합점수 → 고정컷 라벨 |
| `config.py` | env 로딩 + 상수(게이트·창·가중치·길이임계값) |
| `licensed_video_classification.xlsx` | 작품 분류 seed (271작품, `--seed-works` 입력) |
| `.env` | 모든 키 자족 (LAEEBLY_DB_URL·PIPELINE_*·GEMINI_*) |

## 설정 (1회)

0. **대상 테이블 생성** (새 pipeline DB에서 최초 1회): Supabase 대시보드 →
   video-improvement-pipeline → **SQL Editor** → [eb_example_bank_tables_v0.1.sql](eb_example_bank_tables_v0.1.sql)
   내용을 붙여넣고 Run → `eb_*` 5개 테이블 생성. (PostgREST는 이미 있는 테이블만 노출 —
   이걸 안 하면 첫 적재부터 실패. 이미 만들어둔 DB면 건너뜀.)
   Storage 버킷 `laeebly-shorts-video`도 미리 있어야 함(mp4 아카이브 대상).
1. 의존성: `pip install -r requirements.txt`  (버전은 requirements.txt 참조)
   (+ 시스템에 `yt-dlp`·`ffmpeg`·`ffprobe` — pip 아님, OS 패키지매니저로 별도 설치)
2. `factory/.env`의 `LAEEBLY_DB_URL` `[비밀번호]` 자리만 채운다:
   ```
   LAEEBLY_DB_URL=postgresql://postgres:[비밀번호]@db.mehvzxzajydffflqcuuk.supabase.co:5432/postgres
   ```
   Supabase 대시보드 → laeebly → **Connect**의 완성 URI를 복사하는 게 가장 확실.
   (게이트/+7D창/채널기저 집계가 SQL이라 직결 필요. 코드는 `set_session(readonly=True)`로 쓰기 차단)
3. PIPELINE_*·GEMINI_* 키는 `factory/.env`에 이미 포함(독립). 필요 시 그 파일에서 수정.

## 실행

```bash
python run_factory.py --seed-works         # (최초 1회) 분류 xlsx → eb_ip 선적재
python run_factory.py --dry-run            # 게이트 통과/신규 수만 확인 (쓰기 0)
python run_factory.py --limit 3 --no-vlm   # 결정론만 3개 — 배선 스모크 테스트
python run_factory.py --limit 5            # VLM 포함 5개 — 첫 실전 검증
python run_factory.py --limit 0            # 신규 전부 (⚠ 3만+ × Gemini 비용/시간 사전 계산)
python run_factory.py --score-only         # 재채점만 (적재 후 분포 바뀌면 주기 실행)
python run_factory.py --ids <content_id>   # 특정 쇼츠 디버그
```

멱등: 이미 `eb_shorts_features`에 있는 shorts_id는 자동 skip. 중단 후 재실행하면 이어서 처리.
영상이 비공개/삭제면 `lifecycle_status`만 기록하고 성과·통제 레코드는 그대로 적재.

## 원작(IP) 클러스터 (eb_ip)

클러스터 = **포맷(축1) × 서사구조(축2)**, grain=원작(IP) 1편, PK=`ip_key`.
원작을 **라이선스 유무 무관하게** 담고 `is_laeebly_licensed`로 구분. classify는 라이선스/비라이선스
두 경로로 분기(둘 다 eb_ip에 쌓이고 캐시로 중복 LLM 방지):

- **라이선스**(`identification_code` 有, 57.7%): laeebly 코드로 조회 → 없으면 메타로 Gemini 분류.
  `has_source_video=true`(원본 영상물 클립).
- **비라이선스**(코드 無, 42.3%): **description(1순위)+영상**으로 Gemini가 원작 식별 →
  `ip_key='t:'+정규화제목`으로 eb_ip 등록. 설명란만으로 conf≥`IDENTIFY_TEXT_CONF`면 **영상 생략(B방식)** —
  화면(배우 얼굴)이 설명란을 덮어써 오판하는 걸 방지. `identification_code`로 못 오는 원작을 VLM이 대신 식별.
- **자체제작**(원본 영상물 편집 아님 — 밈·이슈정리·이미지+TTS 논평 등): `has_source_video=false`,
  `cluster_id=null`, IP 등록 안 함. 회사 관심대상은 드라마·영화·예능 등 **원본 편집 클립**뿐.
  (VLM 판단 오류 대비 배제 대신 적재+플래그로 구분 — 나중에 필터/재분류.)

→ **같은 원작 = 같은 클러스터** (라이선스=코드 / 비라이선스=정규화 제목 키). 정서톤(축3)은 `tone_tags`(condition).
최초 `--seed-works`로 분류 xlsx(271작품)를 `xlsx_seed`로 선적재. 백필: `python backfill_clusters.py`
(cluster 없는 쇼츠를 저장 description+로컬영상으로 재분류).

## 게이트 (⚠ upload_at = 데이터 날짜 기준, 진짜 쇼츠만)

```
video_length <= 180s                  -- 쇼츠 길이 (롱폼/중간 배제, config SHORTS_MAX_SEC)
AND sum(impressions) > 0              -- ★쇼츠 판별: 게시물/이미지슬라이드(노출 0) 배제
AND min(upload_at) <= publish_time + 1d   -- 발행 초기 데이터 존재 (좌측절단 배제)
AND max(upload_at) >= publish_time + 7d
```
`created_at`(적재시각, ~4일 지연)을 쓰면 오판. 2026-07-07 실측: upload_at 게이트 32,399 →
길이≤180 → 31,635 → **impressions>0 → 31,540** (게시물 95개 제외). 성과 창도 `upload_at ∈ [publish,+7d]`.

> **쇼츠 판별 근거**: laeebly youtube_studio는 쇼츠·일반영상·커뮤니티 게시물을 동일 스키마로
> 섞어 담아 길이만으론 구분 불가. 표본 45/45에서 `impressions=0`=게시물(피드 노출 없음),
> `>0`=진짜 쇼츠로 완벽 분리 → 유일한 SQL측 쇼츠 판별자. (채널 기저 pool에도 동일 필터 적용)

## 점수 v0 (scoring.py)

**lift 분모 = laeebly 완전 채널 이력.** 우리 적재분(eb_shorts_features, 부분집합)이 아니라
laeebly 원천에서 **그 채널 모든 쇼츠(≤180s, +7D)의 trailing 90d 중앙값**(자신 제외)으로 나눈다
— 채널의 진짜 norm. 채널 표본이 진짜 <5(신규채널)면 `lift=null`(cluster/global 폴백 안 씀:
lift 목적=채널 파워 제거라 채널 밖 기저는 의미 깨짐). `baseline_cascade`: `channel`|null.

이어서: 모집단(같은 원작 n≥10 → **클러스터** → 전체) 백분위 → 가중합 → **고정컷**
(<33 bad · 33~66 mid · ≥66 good, `LABEL_CUTS`). 가중치(`SCORE_WEIGHTS`):
**lift .25**(채널 대비 배수) · **절대조회 .15**(실제 도달 규모) · 계속시청 .4 · 참여 .2 — 결측 재분배.
> ★조회를 상대(lift)+절대 둘로 쪼갬(v0.3). lift만 쓰면 대형 채널의 큰 도달(예: 80만)이 자기 채널
> 평균 미달이라 부당하게 낮아짐 → 절대 조회수 백분위를 15% 얹어 실제 규모를 반영.

클러스터/전체 폴백은 **백분위 쪽에만** 남음. `--score-only`도 lift 분모가 laeebly라 `LAEEBLY_DB_URL` 필요.

### 채점 시점 — as-of 2모드 (v0.2)

백분위 "또래"를 언제 기준으로 잡느냐가 두 국면으로 갈린다:

| 모드 | 대상 | 백분위 모집단 | 언제 |
|---|---|---|---|
| **mutual** | 전부 재채점 | 전체(상호, 시간필터 없음) | 백로그 최초 1회 / 재베이스라인 |
| **asof**(기본) | 신규(미채점)만 | **발행 이전(`pt≤`)만**, 자신 제외 | 평상시 — 라벨 고정, 대규모 재채점 회피 |

- 신규는 한 번 채점되면 라벨 **고정**(미래 쇼츠가 옛 라벨 안 바꿈). 이미 채점분은 저장된 `lift_views` 재사용.
- as-of 필터는 캐스케이드 각 단(원작→클러스터→전체)에 걸린다. lift(정규화①)는 원래부터 as-of(trailing).
- CLI: `--score-mode asof|mutual` (기본 asof).

**운영 흐름**:
```bash
python run_factory.py --limit 0 --no-score          # ① 백로그 적재 (채점 끔)
python run_factory.py --score-only --score-mode mutual   # ② 백로그 채점 1회 (상호)
python run_factory.py --limit N                     # ③ 이후 평상시 (신규만 as-of, 기본)
```

## 남은 것 / 주의

- **웨이브2 지표**(kept_watching_rate·likes 등)는 2026-06-19 이후 발행분만 존재 → 게이트 후보의
  ~93%가 계속시청 없음. **처리**: 풀 분리 없이 전부 한 모집단에서 채점하되, `score_basis` 태그로
  `full`(계속시청 반영) / `reach_only`(도달·참여만) 구분 → 인출 시 `full` 우선. lift·절대조회는
  태그 무관 전체 공유(표본 최대화), 계속시청 백분위만 있는 것끼리.
- **비라이선스 원작 분류**: laeebly 라이선스 아닌 원작(identification_code null)의 source work를
  쇼츠에서 추출해 `eb_ip`에 등록·클러스터화하는 건 후속 과제(현재 cluster=null, 쇼츠는 적재됨).
- `eb_channels.dossier`(L2 아티팩트)는 이 공장 범위 밖(별도 생성 패스).
- 전량 실행 전 비용: 후보 수 × Gemini 영상 호출 단가로 사전 계산할 것.
