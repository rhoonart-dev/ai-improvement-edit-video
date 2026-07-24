# DB 정리 원장 (fdidiqd 파이프라인 DB)

> 운영 중 생긴 **삭제/정리 대상 레코드**를 추적하는 문서. 프로덕션 DB(fdidiqd)라 아무나 지우지 않고
> 여기에 등재 → 확인 → 처리 → 완료 표시. **파괴적 삭제(DELETE)는 자동화 에이전트가 실행 차단되므로
> 사람이 직접** Supabase SQL Editor(fdidiqd) 또는 psql로 실행한다. (UPDATE는 대체로 허용)
> 최초 작성: 2026-07-24.

## 처리 절차

1. 아래 "대기 항목"에 등재 (id·이유·SQL·상태).
2. 삭제 전 **확인 SELECT**로 대상 1건이 맞는지 검증.
3. 트랜잭션(`BEGIN … COMMIT`)으로 자식 테이블 먼저, 부모(`clips`) 나중에 삭제.
4. **검증 SELECT**로 잔여 확인 → 상태를 `완료`로, "완료 항목"으로 이동.

주의:
- `clips`는 부모. 참조 자식(운영 중 관측된 것): `judge_runs`, `clip_metadata`, (뷰) `v_training_matrix`.
  그 외 clip_id 참조 테이블: `clip_features`, `clip_performance`, `golden_human_labels`,
  `improvement_directives`, `reward_scores` — 해당 clip에 행이 있으면 함께 삭제.
- `works` 행은 다른 clip이 참조할 수 있으니 **작품 자체는 삭제 금지**.
- `video_external_id`(YouTube content_id)는 이 DB 삭제로 안 지워짐 → **YouTube 영상은 Studio에서 별도 삭제**.

---

## 대기 항목 (미처리)

### 1. 샤먼: 미신전 — 깨진 8.6초 clip

| 항목 | 값 |
|---|---|
| clip_id | `c71b44f4-ca7b-4fcd-a56b-52023179a8f4` |
| 작품 | 샤먼: 미신전 |
| 이유 | 폴백 버그(원본 911초 초과 payoff)로 payoff 소실 → **8.6초 반쪽 영상**. DB엔 계획값 `dur=55.5`로 잘못 기록됨 |
| YouTube | `5uvOMEBrqag` (사람이 Studio에서 별도 삭제) |
| 대체 최종본 | clip `905123c7…` → `KiLHPGsl7jI` (정본 소스, TTS 자막 포함) |
| 상태 | ⏳ 미처리 (자동 DELETE 차단됨 → 수동) |

확인:
```sql
SELECT w.title, c.id, c.duration_sec, c.video_external_id
FROM clips c JOIN works w ON w.id = c.work_id
WHERE c.id = 'c71b44f4-ca7b-4fcd-a56b-52023179a8f4';
-- 기대: 샤먼: 미신전 | c71b44f4… | 55.5 | 5uvOMEBrqag
```

삭제:
```sql
BEGIN;
DELETE FROM judge_runs    WHERE clip_id = 'c71b44f4-ca7b-4fcd-a56b-52023179a8f4';  -- 1행
DELETE FROM clip_metadata WHERE clip_id = 'c71b44f4-ca7b-4fcd-a56b-52023179a8f4';  -- 1행
DELETE FROM clips         WHERE id      = 'c71b44f4-ca7b-4fcd-a56b-52023179a8f4';  -- 1행
COMMIT;
```

검증:
```sql
SELECT c.id, c.duration_sec, c.video_external_id
FROM clips c JOIN works w ON w.id = c.work_id
WHERE w.title = '샤먼: 미신전' ORDER BY c.created_at;
-- 기대: 905123c7… | 59.7 | KiLHPGsl7jI  (한 행만)
```

---

## 완료 항목

_(없음 — 처리 후 여기로 이동)_

---

## 참고: 이번 운영에서 생긴 잔여물(삭제 아님, 기록)

- **gen_queue** 숏나우저 `놀라운 토요일` ep425 → `status='done'`, `run_id='놀라운_토요일_48'`로 **완료 처리됨**(UPDATE, 2026-07-24). 삭제 대상 아님.
- DB 링크 끊긴 옛 YouTube 영상: `e_NmI8FHd24`(숏나우저 ASR자막본)·`y1nMyBpTIuo`(샤먼 TTS자막 없던본) — DB엔 흔적 없음, YouTube에서만 삭제하면 됨.
