# 대시보드 검수함 스펙 — 검수 결정을 1급 데이터로

> 대시보드 구현 담당에게 전달하는 요구사항. 작성 2026-08-04 (맥1 세션).
> 현행 대시보드: `deploy/edge-dashboard/` (Supabase Edge Function `dashboard`, fdidiqd).
> 관련 정본: `SCENE_LOOP_OPERATIONS.md`(루프 운영) · `scripts/send_heartbeat.py` 머리 주석(하트비트 페이로드).

---

## 0. 배경 — 지금 검수가 어떻게 돌고, 무엇이 문제인가

**현행 워크플로우 (운영자 실측, 2026-08-04):**

1. 밤에 launchd 가 채널당 1편을 생성한다 (6대, `scene_loop.py`).
2. 산출물을 **일부공개(unlisted)로 유튜브에 올린다** — 운영자가 Finder 에서 폴더를 뒤져
   `shorts.mp4` 를 여는 게 번거로워, **유튜브 채널을 검수 뷰어로 쓰고 있기 때문**이다.
3. 운영자가 채널에서 보고: 합격 → Studio 에서 **공개(public)** / 불합격 → **비공개(private)**.
4. 루프는 공개 수(회차당 3개)로 회차를 진행하고, 미공개 대기 3개가 쌓이면 생성을 멈춘다.

**문제**: 루프가 "사람의 반려"를 유튜브 privacy 상태에서 **추론**한다. 조회 수단(공개 API 키)이
private 을 아예 못 봐서 '반려'와 '예약 공개 대기'가 구분이 안 됐고, 7일 유예로 때우는 동안
반려분이 대기 슬롯을 점유해 **생성이 며칠씩 멈췄다**(2026-08-04 맥1 4채널 전멸 실측).

2026-08-04 에 조회를 채널 OAuth 로 바꿔(scene_loop.py `youtube_statuses_owner`) 반려/예약이
즉시 구분되게 응급처치했다. **그러나 이것도 여전히 추론이다.** 대시보드의 역할은 추론을
없애는 것 — **검수 결정을 사람이 직접 기록하는 1급 데이터로 만든다.**

---

## 1. 원칙

1. **결정은 DB 에, 추론은 폴백으로.** 사람의 합격/반려는 fdidiqd 에 명시적 레코드로 남긴다.
   유튜브 privacy 는 결정의 *집행 결과*이지 결정의 *저장소*가 아니다.
2. **6대가 같은 판정을 본다.** 지금은 판정 근거(`results/scene_publish_state.json`)가 머신별
   파일이라 서로 못 본다. 중앙 DB 로 옮기면 이 분단이 사라진다.
3. **루프의 브레이크 설계는 유지한다.** 회차당 공개 3개·대기 상한 3개·공개 전환은 사람 —
   이 구조는 바꾸지 않는다. 대시보드는 그 "사람" 단계를 빠르고 정확하게 만들 뿐이다.

---

## 2. 1단계 — 결정 버튼 (유튜브 뷰어는 그대로)

현행 워크플로우를 바꾸지 않고 결정 기록만 추가한다. **구현 최소, 즉시 효과.**

### 2-1. 데이터

fdidiqd 에 테이블 추가 (마이그레이션은 `docs/migrations/` 규약대로, 적용은 사람 확인 후):

```sql
create table review_decisions (
  id            uuid primary key default gen_random_uuid(),
  clip_id       uuid not null references clips(id),
  decision      text not null check (decision in ('approved','rejected')),
  decided_at    timestamptz not null default now(),
  decided_by    text,                    -- 대시보드 접속자 식별(자유 텍스트면 충분)
  note          text,                    -- 반려 사유(선택) — 나중에 개선 신호로 쓸 수 있다
  unique (clip_id)                       -- 최신 결정만 유지(upsert). 이력이 필요해지면 그때 완화
);
```

- `clip_id` 기준인 이유: run_id ↔ clip ↔ video_external_id 링크가 이미 clips/clip_metadata 에
  있다. 장면(run_id)이 아니라 clip 단위가 발행·판정의 기존 키다.
- **반려 사유(note)는 선택 입력**이되 UI 에 넣어라 — "왜 버렸는지"가 쌓이면 그 자체가
  생성 품질 개선의 관측 데이터가 된다(단, 관측→규칙 직행 금지 원칙은 그대로).

### 2-2. UI (기존 대시보드에 탭 하나)

**검수함 탭**: 검수 대기 = `clips.video_external_id is not null` 이고 `review_decisions` 에
행이 없는 클립. 채널/머신별 그룹.

각 항목:
- **유튜브 임베드**(`https://www.youtube.com/embed/<video_external_id>`) — unlisted 는 임베드
  재생이 된다. 채널 들어가서 찾는 단계가 없어진다.
- 메타: 채널 · 작품 · 회차 · 구간(origin_start~end) · 길이 · judge quality/환각 · 생성일.
- 버튼: **✅ 합격** / **❌ 반려**(사유 입력 선택) → `review_decisions` upsert.
- 합격 후 안내: "Studio 에서 공개 전환 필요" 링크(`https://studio.youtube.com/video/<id>/edit`).
  ※ 공개 전환 자동화는 하지 않는다 — 현행 토큰 scope 로 불가하고(§4 참고), 공개는 사람
  개입 지점이라는 운영 설계를 유지한다.

### 2-3. 루프 연동 (brain 쪽 — 대시보드 담당 범위 아님, 참고)

`classify_scenes` 가 `review_decisions` 를 최우선 신호로 본다:
- `rejected` → 즉시 SCENE_REJECTED (유튜브 조회 불필요)
- `approved` 인데 아직 unlisted → SCENE_PENDING (공개 전환 대기)
- 행 없음 → 현행 OAuth 조회로 추론 (폴백)

이미 발행 기록의 `rejected_at` 을 조회보다 우선하는 훅이 들어가 있어(2026-08-04 패치),
DB 를 읽어 같은 필드를 채우면 된다.

### 2-4. 하지 말 것

- ⛔ 대시보드가 유튜브 privacy 를 **변경**하는 것 (공개 전환은 사람이 Studio 에서).
- ⛔ 결정 없이 privacy 만 바꾸는 운영으로의 회귀를 막지 마라 — 운영자가 Studio 에서만
  비공개로 돌리고 대시보드를 안 누르는 날도 루프는 OAuth 추론 폴백으로 동작해야 한다.
- ⛔ `review_decisions` 를 성과 판정(승격)에 쓰는 것 — 검수는 안전·품질 게이트일 뿐,
  성과 검증은 코호트 벤치마크/쌍 A/B 만이다(CLAUDE.md §7).

---

## 3. 2단계 — 유튜브를 검수 경로에서 빼기 (합격작만 업로드)

1단계가 자리 잡은 뒤. **폐기될 영상까지 유튜브에 올리는 현행 구조 자체를 없앤다.**

- 각 머신이 렌더 직후 `shorts.mp4` 를 **Supabase Storage** 버킷에 올린다
  (하트비트 스크립트에 붙이거나 별도 업로더 — 편당 ~30MB, 머신당 하루 최대 4편 ≈ 120MB).
- 대시보드 검수함이 Storage URL 로 직접 재생한다. 메타는 하트비트의 `state_snapshot` −
  `publish_snapshot` 차집합(③ 렌더만 되고 미업로드 — 2026-08-04 A안)으로 이미 계산 가능.
- **합격 → 그때 비로소 발행 파이프라인**(ingest → judge → unlisted 업로드 → 사람 공개).
  반려 → 유튜브에 아예 올라가지 않는다. 채널에 폐기물 이력이 안 남는다.
- Storage 보존 정책: 결정 후 N일(예: 14일) 지나면 삭제(공간 관리). 산출물 원본은 어차피
  각 머신 `outputs/scene_loop/` 에 있다.
- 이 단계가 되면 루프의 "공개 카운트" 입력도 review_decisions + 발행 기록으로 대부분
  대체되고, 유튜브 조회는 최종 공개 확인용으로만 남는다.

---

## 4. 제약·주의 (실측 근거)

| 항목 | 내용 |
|---|---|
| 토큰 scope | 6대 발행 토큰은 `youtube.upload`+`youtube.readonly` 뿐 — **videos.update(공개 전환) 불가**(2026-08-04 맥1 4채널 실측). 대시보드에서 공개 전환을 구현하려면 채널별 재동의가 선행돼야 하며, 현행 운영 설계상 하지 않는다 |
| unlisted 임베드 | 가능(private 은 불가). 1단계 임베드는 unlisted 상태에서만 동작한다 — 검수 전에 private 으로 돌리면 임베드가 깨진다 |
| 예약 공개 | `scene_publish_loop` 기본 동작이 예약 공개(publishAt)다. 예약분은 공개 시각 전까지 private — 검수함에서 "예약 대기"로 별도 표기할 것(반려로 오인 금지) |
| 채널 상수 | 대시보드의 CHANNELS 하드코딩은 정본(`config/channels.json`) 스냅샷이다 — 채널 추가 시 갱신 필요(드리프트 주의) |
| 인증 | 현행 접속 코드(DASHBOARD_PASSWORD) 방식 유지. 결정 API 도 같은 인증 뒤에 둘 것 — 결정은 쓰기 작업이다 |
| 삭제 | 대시보드는 어떤 것도 삭제하지 않는다. DB 정리는 `DB_CLEANUP_LEDGER.md` 절차(사람 수동)만 |

## 추가 §5 — 검수함에 judge 결과 표시 (2026-08-05, 표시 전용)

배경: judge(LLM 안전·품질 평가)가 검수 **전** 야간에 선실행되도록 바뀌었다(맥 쪽 완료).
검수함 카드에 그 결과를 **표시만** 한다. 자동 합격/반려·기본 필터링 금지 — 결정은 100% 사람.

### 데이터 (fdidiqd `judge_runs`, clip_id 로 최신 1행)

```sql
SELECT DISTINCT ON (clip_id) clip_id, quality_score, confidence, judge_model,
       rubric_scores, created_at
FROM judge_runs ORDER BY clip_id, created_at DESC
```

`rubric_scores` jsonb: `hook_3s`·`visual_hook`·`pacing`·`completion_pull`(0~1) ·
`rationale`(사유 텍스트) · `hallucination_flag`(bool) · `hashtags_ok`(bool)

### 표시 요구

1. 검수함 카드에: quality 종합점수 + 세부 4점수 + `rationale` 전문 + confidence.
2. `hallucination_flag=true` 면 눈에 띄는 경고 배지 — 문구 예: "⚠ 환각 의심 — 합격해도
   발행 게이트에서 자동 차단됨". (발행 차단은 기존 안전게이트가 하며 대시보드 일이 아님)
3. judge 행이 없으면 "judge 대기중" 표기 — **결정 버튼은 그래도 활성**이어야 한다
   (선실행 실패·지연이 검수를 막으면 안 된다).
4. ⛔ 하지 않는 것: judge 점수로 자동 결정·정렬 기본값 강제·발행 트리거. quality 는
   성과 예측이 아니다(CLAUDE.md §7) — 참고 정보로만.

### 선택(나중): 사람-LLM 판단 비교 뷰

`review_decisions` × `judge_runs` 를 clip_id 로 조인하면 사람 결정(approved/rejected + note)과
judge 점수·사유가 짝으로 나온다. 일치율·불일치 사례 테이블 하나면 충분하다. 용도는 검수 보조와
judge 개선(결함 유형 발견)이며, ⛔ 성과 판정·승격에는 쓰지 않는다.
