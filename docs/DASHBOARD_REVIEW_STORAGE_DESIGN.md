# 검수 2단계 설계 — Storage 기반 검수 (유튜브를 검수 경로에서 뺀다)

> `docs/DASHBOARD_REVIEW_INBOX_SPEC.md` §3(2단계)의 구체화. 작성 2026-08-05.
> 결정 사항: **1단계(유튜브 임베드 검수)는 건너뛴다** — 운영자가 일부공개 검수 관행을
> 이미 정리했고(2026-08-05), 임베드 화면은 만들어도 며칠 쓰고 버릴 경로이기 때문.
> 관련 정본: `SCENE_LOOP_OPERATIONS.md`(루프 운영) · `scripts/send_heartbeat.py`(하트비트)
> · `deploy/edge-dashboard/`(대시보드 API·화면).

---

## 0. 목표 — 한 문단

밤에 렌더된 쇼츠를 **각 맥이 Supabase Storage 에 올리고**, 운영자는 대시보드 검수함에서
**바로 재생해 합격/반려를 누른다.** 결정은 `review_decisions`(fdidiqd)에 1급 데이터로 남고,
**합격작만** 발행 파이프라인(ingest→judge→유튜브 업로드)을 탄다. 반려작은 유튜브에 아예
올라가지 않는다 — 채널에 폐기물 이력이 안 남고, "일부공개 = 검수 대기" 같은 추론이 사라진다.

```
[각 맥, 야간]                         [대시보드]                    [각 맥, 10분 픽업]
scene_loop 렌더 확정
  → ① mp4 를 Storage 업로드          ② 검수함: 서명 URL 로 재생
  → ① clips/clip_metadata 적재         + judge 점수·사유 표시(참고용)
  → ① judge 선실행(2026-08-05)            ✅합격/❌반려 → review_decisions
     (video_external_id 는 아직 NULL)                                ③ approved && 미발행 픽업
                                                                     → judge(선실행분 있으면 생략)
                                                                     → 유튜브 업로드
                                                                     → video_external_id 채움

★ judge 선실행(2026-08-05 운영자 결정): 검수함에 LLM 점수·사유가 **함께 보이도록** 업로더가
  야간에 judge 를 먼저 돌린다(미발행·미결정·미judge 클립만 — 결정난 건 비용 절약으로 생략).
  **표시 전용이다** — 합격/반려는 100% 사람이고, judge 는 발행 직전 안전게이트(환각 차단)
  역할만 유지한다. 사람 결정(review_decisions)과 judge(judge_runs)가 클립별로 쌓이므로
  사람-LLM 판단 비교가 조인 한 번으로 가능해진다.
```

## 1. 원칙 (스펙 §1 계승 + 이 설계의 추가)

1. **결정은 DB 에** — 합격/반려는 `review_decisions` 가 정본. 유튜브 상태 추론은 폴백으로만.
2. **업로더는 생성을 절대 막지 않는다** — 하트비트와 같은 원칙. 실패는 다음 실행이 재시도.
3. **발행 실행은 각 맥** — 대시보드는 결정을 기록할 뿐, 유튜브에 손대지 않는다. 토큰이
   각 맥에만 있기도 하지만, 더 중요한 건 `publish_youtube.py` 의 안전장치들(오채널 하드
   차단·지오블락 독립 판정·작품명 완전일치·설명란 필수 표기)이다 — 웹 발행은 이걸 Deno 로
   한 벌 더 만드는 일이고, 두 벌은 한쪽만 고쳐지는 순간 구멍이 된다(폴백 이중화 사고 전례).
   **체감 즉시성은 픽업 주기로 해결한다**: `scene_publish_loop` 을 launchd 로 **10분 주기**
   실행 → 합격 버튼 = "발행 예약", 실제 발행은 최대 10분 내(§6). 주기를 10분으로 두는 이유:
   통합 아키텍처(ves-architecture, 2026-08-04)의 워커 폴링 주기와 같다 — 나중에 ves-agent 로
   갈아타도 운영자 체감이 안 바뀐다.
4. **인제스트를 렌더 직후로 앞당긴다** — 지금은 발행 때 인제스트라 미발행분이 DB 에 없어
   하트비트 스냅샷 차집합으로 우회했다(2026-08-04 A안). 업로드 시점에 `clips` 행을 만들면
   검수 큐가 DB 쿼리 한 방이 되고 A안 우회는 자연 폐기된다.
5. **삭제는 자동으로 하지 않는다** — 보존 정리는 목록을 뽑아 사람이 실행(§6).

## 2. 조각 A — Storage 버킷 `review-clips`

| 항목 | 값 | 왜 |
|---|---|---|
| 버킷 | `review-clips` (fdidiqd) | 기존 `laeebly-shorts-video`(시장 쇼츠 아카이브)와 용도 분리 |
| 공개 여부 | **private** | 재생은 대시보드 API 가 발급하는 **서명 URL**(만료 1시간)로만 |
| 경로 규약 | `<machine_id>/<clip_id>.mp4` (예 `macmini-luna4/333fbf38-….mp4`) | ⚠️ run_id 는 한글이라 못 쓴다 — Storage 가 키의 한글을 InvalidKey 로 거부(2026-08-05 실측, percent-encode 로도 불가). clip_id(uuid)는 ASCII 확정 + 전역 유일. run_id 추적은 clips.storage_path·clip_metadata 로 |
| 용량 | 편당 ~30MB × 6대 × 최대 4편/일 ≈ **월 ~22GB 유입** | 보존 정리(§6) 없이는 계속 자람 — 요금 주의 |

정책: anon/authenticated 접근 없음. 쓰기는 각 맥이 service key 로(PostgREST Storage API),
읽기는 Edge Function(service role)이 서명 URL 생성으로만.

## 3. 조각 B — 업로더 `scripts/upload_review_clips.py` (각 맥)

**스캔 방식**: 상태파일(`scene_loop_state.json`)의 확정 장면 중 ①Storage 에 없고 ②아직
발행 안 된 것을 찾아 → mp4 업로드 → `ingest_aivideo_run.py` 로직으로 `clips`+`clip_metadata`
적재(있으면 skip — 멱등) → `clips.storage_path` 기록.

- **배선**: `scene_loop_run.sh` 의 종료 하트비트 직전에 1회 호출(`|| true`). 기존 코드
  미변경 원칙과 하트비트 패턴 그대로.
- **스캔 방식인 이유**: 실행마다 밀린 것을 전부 집으므로 ①업로드 실패가 다음 실행에서 자연
  재시도되고 ②**기존 백로그(미업로드 렌더 ~20건)가 첫 실행 때 자동으로 흡수**된다.
- 자격증명: `PIPELINE_URL` + `PIPELINE_SERVICE_KEY` — **루트 `.env` 가 정본**(2026-08-05
  결정: factory 는 전 맥용이 아니라 factory/.env 를 강제하지 않는다. 있으면 폴백으로만 읽음).
  업로드 경로 자체는 `factory/db.py:storage_upload` 와 동일(PostgREST Storage API).
  없으면 로그 남기고 skip — 생성을 안 막는다.
- 재배정 잔재(stale)·정본에 없는 채널명은 업로드하지 않고 경고만(대시보드 anomalies 와 동일 규칙).

## 4. 조각 C — DB (마이그레이션 0008, 적용은 사람 확인 후)

```sql
-- 0008_review_decisions.sql (초안 — 실제 파일은 docs/migrations/ 규약대로 주석 포함)
CREATE TABLE review_decisions (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  clip_id    uuid NOT NULL REFERENCES clips(id) UNIQUE,  -- 최신 결정만(upsert)
  decision   text NOT NULL CHECK (decision IN ('approved','rejected')),
  decided_at timestamptz NOT NULL DEFAULT now(),
  decided_by text,          -- 대시보드 접속자 표시명. 소급 입력은 'backfill:…' 로 구분
  note       text           -- 반려 사유(선택) — 쌓이면 생성 품질의 관측 신호
);
ALTER TABLE clips ADD COLUMN storage_path text;  -- 'review-clips/<machine_id>/<clip_id>.mp4'
```

- `decision` 은 스펙대로 2값. 과거 삭제분 백필은 별도 논의(`deleted` 는 `lifecycle_status` 축).
- **검수 큐 정의(단일 쿼리)**: `clips WHERE source='auto_edit' AND video_external_id IS NULL
  AND storage_path IS NOT NULL AND id NOT IN (SELECT clip_id FROM review_decisions)`.

## 5. 조각 D — 대시보드 (Edge Function + React)

| API | 동작 |
|---|---|
| `GET /api/review` | 검수 큐(§4 쿼리) + 결정된 최근 N건. 메타: 채널·작품·구간·길이·judge(있으면)·생성일·머신 |
| `GET /api/clip-url?clip_id=` | `storage_path` 로 **서명 URL(만료 1h)** 발급 |
| `POST /api/decision` | `{clip_id, decision, note?, decided_by?}` → `review_decisions` upsert. **첫 쓰기 API** — 같은 접속 코드 인증 뒤, CORS 에 POST 추가 |

화면(검수함 탭): 목업 그대로 카드 + `<video>` 태그(서명 URL) + ✅/❌ + 사유 입력.
회차 번호는 이제 업로더가 인제스트 때 함께 적재하므로 DB 에서 나온다(스냅샷 의존 제거).

## 6. 조각 E — 발행 연동 + 보존 (brain 쪽)

- **합격 → 발행**: `scene_publish_loop` 픽업 조건을 "approved && `video_external_id IS NULL`"
  로 교체(현행 '미발행 전부'에서). 발행 후 privacy 는 현행 예약 공개 흐름 그대로.
- **반려 →**: 발행하지 않음. `classify_scenes` 가 `review_decisions.rejected` 를 최우선으로
  읽어 즉시 슬롯 해제(스펙 §2-3 — 유튜브 조회 불필요). 구간은 중복 회피 대상으로 유지.
- **결정 없음 →**: 발행 보류(검수 대기). 기존 OAuth 추론은 이미 발행된 과거분에만 폴백.
- **픽업 주기**: `scene_publish_loop` 을 launchd **10분 주기** 실행으로 전환 — 합격 후 최대
  10분 내 발행. 야간 생성 스케줄과 독립이라 겹침 없음(픽업 쿼리는 가볍다).
- **보존(2026-08-05 운영자 피드백 반영)** — 상태별로 갈린다:
  | 상태 | 처리 | 근거 |
  |---|---|---|
  | 발행 성공(`video_external_id` 有) | **업로더가 다음 실행 때 자동 정리**(자기 머신 사본만) | 파생 캐시 — 원본이 유튜브+맥 outputs/ 두 곳에 살아있어 정보 손실 없음. "자동 삭제 금지" 원칙의 예외로 운영자 승인 필요 |
  | 반려 | 14일 보관 후 `scripts/list_review_cleanup.py` 가 목록 출력 → 사람이 삭제 | 재확인 여지 + 원칙 유지 |
  | 미결정 | 보관 | 검수 대기 중 |
  발행분이 유입의 대부분이므로 실질 잔존량은 "검수 대기 + 반려 14일치"로 수렴 — §9 비용 위험이 사실상 해소된다.

## 7. 롤아웃 순서 (단계마다 검증 후 다음으로)

1. 마이그레이션 0008 적용(사람 확인) + 버킷 `review-clips` 생성
2. 업로더 + 러너 배선 커밋 → 6대 pull → 맥 1대에서 수동 실행으로 백로그 업로드 검증
3. 대시보드 API(v3: review·clip-url·decision) 배포 + React 검수함 탭 → 실검수 1건으로 왕복 확인
4. `scene_publish_loop` 픽업 조건 교체 + `classify_scenes` 연동 — **이때부터 합격작만 발행**
   (순서 중요: 3까지는 발행 동작이 안 바뀌므로 아무 때나 되돌릴 수 있다)

## 7-1. 통합 아키텍처(ves-architecture v1, 2026-08-04)와의 관계

이 설계는 통합 아키텍처로 가는 길에 **버려지지 않는다** — 각 조각이 그 아키텍처의 전신이다:

| 이 설계 | 통합 아키텍처에서 |
|---|---|
| launchd 10분 픽업("approved && 미발행") | §3 Pull 폴링의 수동 전신 — Phase 1 에서 ves-agent 의 `job_queue` claim 으로 **대체** |
| `review_decisions` + 합격/반려 버튼 | §4 **publish_gate(사람①)** 의 결정 저장소로 **그대로 승계** ("승인 → publish 자동 실행"과 동일 UX) |
| Storage `review-clips` + `clips.storage_path` | `artifacts`(산출물 카탈로그)의 전신 |
| machine_heartbeats | `node_registry` 심박의 전신 |

주의: 통합 아키텍처는 채널 고정 배정을 없애지만(동질 워커 풀), 이 설계의 픽업은 당분간
배정 정본을 따른다(토큰이 머신별이므로). 대시보드·DB 쪽은 배정에 의존하지 않게 유지한다.

## 8. 하지 않는 것 (스펙 §2-4 계승)

⛔ 대시보드가 유튜브 privacy 를 변경 · ⛔ 무엇이든 자동 삭제 · ⛔ `review_decisions` 를 성과
판정(승격)에 사용(성과는 코호트/쌍 A/B 만 — CLAUDE.md §7) · ⛔ 대시보드에서 발행 트리거.

## 9. 위험과 대응

| 위험 | 대응 |
|---|---|
| Storage 비용 누적(월 ~22GB 유입) | 발행분 자동 정리(§6)로 잔존량이 검수 대기+반려 14일치로 수렴. 반려분만 주기 수동 정리 |
| 업로드 실패로 검수함에 안 보임 | 스캔 방식이라 다음 실행 재시도. 하트비트 채널 결과와 대조하면 "렌더됐는데 검수함에 없음"이 대시보드에서 드러남 |
| POST 개방(첫 쓰기 API) | 접속 코드 필수 + upsert 대상이 `review_decisions` 한 테이블뿐. 삭제·수정 API 없음 |
| 회차 진행이 검수 속도에 종속 | 의도된 설계(브레이크 유지). 검수함이 그 속도를 올리는 수단 |
