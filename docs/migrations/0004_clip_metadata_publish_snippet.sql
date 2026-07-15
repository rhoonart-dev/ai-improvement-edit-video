-- 0004_clip_metadata_publish_snippet.sql — §3-8 발행 스니펫 기록 (provenance 완결성)
--
-- 발행 시 실제 사용된 YouTube snippet(title/description/tags)을 클립에 남긴다.
-- K7 로 제목·해시태그 실험은 동결이지만, '무엇이 발행됐는지'는 provenance 로 항상 기록해야
-- 나중에 도달 지표 판정 채널이 열렸을 때(K7 재개) 과거 발행분을 소급 분석할 수 있다.
--
-- ⚠️ 적용은 사용자 확인 후. 격리 파이프라인 DB(ref fdidiqdhcyctdbogxkdu)에만 적용. 가산적.

ALTER TABLE public.clip_metadata
  ADD COLUMN IF NOT EXISTS publish_snippet jsonb;

-- 형태: {"title": "...", "description": "#작품명", "tags": ["작품명"], "categoryId": "24",
--        "channel": "스토리순삭", "privacy": "unlisted", "published_at": "2026-..."}
-- publish_youtube.upload → link_published(snippet=...) 가 채운다. reconcile 경로는 스니펫
-- 미상이라 NULL(사후 발행 정합분).

COMMENT ON COLUMN public.clip_metadata.publish_snippet IS
  '발행 시 실제 사용된 YouTube snippet + 채널/공개범위 (§3-8 provenance). reconcile 분은 NULL';
