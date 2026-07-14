-- 0005_gen_queue.sql — DB 분단 해소: 생성 큐 테이블을 brain DB(fdidiqd)에 이식
--
-- 배경: gen_queue DDL 이 형제 repo(ai-improve-edit-video)에만 있고 brain DB 에 없어,
--   autogen(--enqueue/--process)이 fdidiqd 에서 UndefinedTable 로 실패했다. 파이프라인을
--   fdidiqd 단일 DB 로 통일하기 위해 이 테이블을 brain DB 에 만들고 이 repo 에 버전관리한다.
--   (형제 repo docs/schema.sql:544 정의와 1:1. 이제 이 파일이 brain 측 정본.)
--
-- ⚠️ 적용은 사용자 확인 후. 격리 파이프라인 DB(ref fdidiqdhcyctdbogxkdu). 가산적.

CREATE TABLE IF NOT EXISTS public.gen_queue (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    work_title  text NOT NULL,
    source      text NOT NULL,                  -- 로컬 파일 경로 또는 youtube url
    channel     text,                            -- 발행 대상 채널(스토리순삭/재미쇼츠)
    topic       text,
    episode     integer,
    max_shorts  integer NOT NULL DEFAULT 1,
    status      text NOT NULL DEFAULT 'pending', -- pending/running/done/failed
    run_id      text,                            -- 생성된 ai-video job_id
    error       text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT gen_queue_status_chk CHECK (status IN ('pending','running','done','failed'))
);
CREATE INDEX IF NOT EXISTS idx_gen_queue_status ON public.gen_queue(status);
ALTER TABLE public.gen_queue ENABLE ROW LEVEL SECURITY;
