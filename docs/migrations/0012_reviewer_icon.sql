-- 0012_reviewer_icon.sql — 검수자 프로필 아이콘
--
-- ⚠️ 적용은 사용자 확인 후. 격리 파이프라인 DB(ref fdidiqdhcyctdbogxkdu)에만 적용. 가산적.
-- ✅ 적용됨 2026-08-06 (MCP apply_migration, 운영자 지시 "작성자 프로필 아이콘 + 선택 목록").
--
-- 아이콘 선택을 localStorage 에만 두면 다른 팀원 화면에서는 안 보인다 — 결정 행에 함께
-- 저장해야 모든 뷰어가 같은 아이콘을 본다. 아이콘 자체(파스텔 얼굴 SVG 세트)는 화면 코드에
-- 내장, DB 에는 id 문자열만. 표시 전용 — 어떤 판정에도 쓰지 않는다.

ALTER TABLE public.review_decisions ADD COLUMN IF NOT EXISTS reviewer_icon text;
COMMENT ON COLUMN public.review_decisions.reviewer_icon IS
  '검수자 프로필 아이콘 id (대시보드 얼굴 아이콘 세트) — 표시 전용, 모든 뷰어에게 동일하게 보이도록 결정과 함께 저장';
