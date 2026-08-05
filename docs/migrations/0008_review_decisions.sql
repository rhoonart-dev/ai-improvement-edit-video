-- 0008_review_decisions.sql — 검수 결정을 1급 데이터로 + Storage 검수 사본 경로
--
-- ⚠️ 적용은 사용자 확인 후. 격리 파이프라인 DB(ref fdidiqdhcyctdbogxkdu)에만 적용. 가산적.
-- ✅ 적용됨 2026-08-05 (MCP apply_migration, 사용자 승인 후).
--
-- 배경: 루프가 사람의 합격/반려를 유튜브 privacy 에서 **추론**해 왔다 — private 을 못 보는
--   공개 API 키 탓에 반려와 예약 공개가 구분이 안 됐고, 반려분이 대기 슬롯을 점유해 생성이
--   며칠씩 멈췄다(2026-08-04 맥1 4채널 실측). 8/4 OAuth 조회 전환은 응급처치일 뿐 여전히
--   추론이다. 이 테이블이 결정의 정본이 된다 — 유튜브 privacy 는 결정의 *집행 결과*이지
--   *저장소*가 아니다. 설계: docs/DASHBOARD_REVIEW_STORAGE_DESIGN.md (스펙: 같은 폴더
--   DASHBOARD_REVIEW_INBOX_SPEC.md §2-1).
--
-- 소비자:
--   쓰기 — 대시보드 Edge Function `dashboard` POST /api/decision (접속 코드 인증 뒤, upsert)
--   읽기 — scene_publish_loop(approved && 미발행만 픽업) · classify_scenes(rejected → 즉시
--          슬롯 해제, 유튜브 조회 불필요) · 대시보드 검수함
--
-- 규칙:
--   - decision 2값(approved/rejected). 소급 백필은 decided_by='backfill:…' 로 실결정과 구분.
--   - 삭제·중복·실수분은 여기 넣지 않는다(clips.lifecycle_status 축 — 품질 반려와 섞으면
--     반려 데이터가 개선 신호로 못 쓰인다).
--   - ⛔ 성과 판정(승격)에 쓰지 않는다 — 검수는 안전·품질 게이트일 뿐(CLAUDE.md §7).
--
-- clips.storage_path: 검수용 Storage 사본 위치('review-clips/<machine_id>/<clip_id>.mp4' —
--   run_id 는 한글이라 Storage 키로 못 쓴다, InvalidKey 2026-08-05 실측).
--   업로더(scripts/upload_review_clips.py)가 기록, 발행 확인 후 사본 자동 정리 시 NULL 로
--   되돌린다(2026-08-05 운영자 승인 — 파생 캐시라 정보 손실 없음. 유튜브 발행본·맥 원본·DB
--   기록은 건드리지 않는다).

CREATE TABLE IF NOT EXISTS public.review_decisions (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clip_id    uuid NOT NULL REFERENCES public.clips(id),
    decision   text NOT NULL,
    decided_at timestamptz NOT NULL DEFAULT now(),
    decided_by text,                 -- 대시보드 접속자 표시명. 소급 입력은 'backfill:…'
    note       text,                 -- 반려 사유(선택) — 쌓이면 생성 품질의 관측 신호
    CONSTRAINT rd_decision_chk CHECK (decision IN ('approved','rejected')),
    CONSTRAINT rd_clip_uniq    UNIQUE (clip_id)   -- 최신 결정만 유지(upsert). 이력 필요 시 완화
);

CREATE INDEX IF NOT EXISTS idx_rd_decision ON public.review_decisions (decision, decided_at DESC);

ALTER TABLE public.review_decisions ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.clips ADD COLUMN IF NOT EXISTS storage_path text;

-- ── 적용 후 점검 ─────────────────────────────────────────────
-- 검수 큐(대시보드 GET /api/review 의 기본 쿼리):
--   SELECT c.id FROM clips c
--   WHERE c.source='auto_edit' AND c.video_external_id IS NULL
--     AND c.storage_path IS NOT NULL
--     AND NOT EXISTS (SELECT 1 FROM review_decisions r WHERE r.clip_id = c.id);
-- 발행 픽업(scene_publish_loop):
--   SELECT c.id FROM clips c JOIN review_decisions r ON r.clip_id = c.id
--   WHERE r.decision='approved' AND c.video_external_id IS NULL;
