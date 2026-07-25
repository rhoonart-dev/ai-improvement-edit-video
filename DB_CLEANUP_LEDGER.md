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
-- 기대(2026-07-26 갱신): c71b44f4 가 사라지고 아래 3행이 남는다.
--   905123c7… | 59.7  | KiLHPGsl7jI   (1화 퇴마 장면, 정본)
--   c7e6d6f3… | 50.37 | SLereobSk4M   ← 아래 2번 항목으로 함께 삭제 대상
--   dc5ded1e… | 50.37 | K88VyA9LZeE   (1화 고백 장면, 자막 제거 최종본)
-- 2번까지 처리하면 905123c7 · dc5ded1e 두 행만 남는 것이 최종 기대 상태.
```

---

### 2. 샤먼: 미신전 — 자막 이중 표기 렌더본 (대체됨)

| 항목 | 값 |
|---|---|
| clip_id | `c7e6d6f3-3f30-47ff-a030-38cf98e57acc` |
| 작품 | 샤먼: 미신전 (`episode='shorts_2'`, 소스 구간 35.46~222.63초) |
| 이유 | **원본 티빙 소스에 자막이 하드번인**되어 있는데 파이프라인 대사자막까지 얹혀 같은 대사가 이중으로 겹쳐 표기됨(11.5초 지점: 원본 "화가 주체가 안 돼요" + 파이프라인 "화가 주체가 안 돼?"). `--no-subtitles` 재렌더본으로 대체 |
| YouTube | `SLereobSk4M` (사람이 Studio에서 별도 삭제) |
| 대체 최종본 | clip `dc5ded1e…` → `K88VyA9LZeE` (동일 장면·동일 구간, 파이프라인 대사자막만 제거. judge 0.875 → **0.975** 상승) |
| 자식 행 | `judge_runs` 1 · `clip_metadata` 1 — `clip_features`/`clip_performance`/`golden_human_labels`/`improvement_directives`/`reward_scores` 는 0건 확인(2026-07-26) |
| 상태 | ⏳ 미처리 (자동 DELETE 차단됨 → 수동) |

확인:
```sql
SELECT w.title, c.id, c.episode, c.duration_sec, c.video_external_id
FROM clips c JOIN works w ON w.id = c.work_id
WHERE c.id = 'c7e6d6f3-3f30-47ff-a030-38cf98e57acc';
-- 기대: 샤먼: 미신전 | c7e6d6f3… | shorts_2 | 50.37 | SLereobSk4M
```

삭제:
```sql
BEGIN;
DELETE FROM judge_runs    WHERE clip_id = 'c7e6d6f3-3f30-47ff-a030-38cf98e57acc';  -- 1행
DELETE FROM clip_metadata WHERE clip_id = 'c7e6d6f3-3f30-47ff-a030-38cf98e57acc';  -- 1행
DELETE FROM clips         WHERE id      = 'c7e6d6f3-3f30-47ff-a030-38cf98e57acc';  -- 1행
COMMIT;
```

검증:
```sql
SELECT c.id, c.episode, c.duration_sec, c.video_external_id
FROM clips c JOIN works w ON w.id = c.work_id
WHERE w.title = '샤먼: 미신전' ORDER BY c.created_at;
-- 기대(1번도 처리 시): 905123c7… | shorts_1 | 59.7 | KiLHPGsl7jI
--                      dc5ded1e… | shorts_3 | 50.37 | K88VyA9LZeE
```

⚠️ 대체본 `dc5ded1e`(K88VyA9LZeE)도 **원본 하드번인 자막은 그대로 남아 있다** — 파이프라인 플래그로 제거 불가.
자막 없는 샤먼이 필요하면 소스를 자막 없는 마스터로 바꾸거나 크롭/블러 처리가 필요(코드·디자인 변경 사안).

---

## 완료 항목

_(없음 — 처리 후 여기로 이동)_

---

## 참고: 이번 운영에서 생긴 잔여물(삭제 아님, 기록)

- **gen_queue** 숏나우저 `놀라운 토요일` ep425 → `status='done'`, `run_id='놀라운_토요일_48'`로 **완료 처리됨**(UPDATE, 2026-07-24). 삭제 대상 아님.
- DB 링크 끊긴 옛 YouTube 영상: `e_NmI8FHd24`(숏나우저 ASR자막본)·`y1nMyBpTIuo`(샤먼 TTS자막 없던본) — DB엔 흔적 없음, YouTube에서만 삭제하면 됨.

### 2026-07-26 운영분 (같은 회차 2차 생성, 모두 unlisted)

- 숏나우저 `놀라운 토요일` ep425 2차 → clip `c1deb277…`(`episode='shorts_2'`) → `iB8VlmQYt5o`.
  같은 귀신퀴즈 코너의 **다른 구간**(857.5~905.1초, 1차는 974~1068초)이라 중복 아님. **삭제 대상 아님.**
  1차와 같은 run(`놀라운_토요일_48`)이라 멱등키 충돌을 피해 라벨을 `shorts_2`로 분리함.
- 여운 보관소 `샤먼: 미신전` 1화 2차 → 위 **2번 항목** 참조(`c7e6d6f3` 폐기 → `dc5ded1e` 대체).
- ⚠️ 운영 메모: `놀라운_토요일_48` run은 variant0(=1차 발행분과 동일 장면)에 대해서만 `edit_plan.json`을 남긴다.
  variant1 이후를 인제스트할 땐 `--edit-plan` 으로 해당 variant용 계획을 지정해야 발행 제목·구간이 어긋나지 않는다
  (2차 발행 시 `edit_plan_shorts_2.json`을 별도 생성해 사용).
- ⚠️ 환경 메모: 렌더 도중 macOS가 `~/Downloads` 읽기를 차단(TCC, `Operation not permitted`)해 샤먼 재렌더가 실패했다.
  Drive 티빙 폴더에서 원본을 `~/ves/sources/shaman_tving/1회_사용구간.mp4` 로 다시 받아 우회함(크기 345,306,408 bytes 동일).
  앞으로 소스는 `~/Downloads` 밖(`~/ves/sources/`)에 두는 편이 안전.
