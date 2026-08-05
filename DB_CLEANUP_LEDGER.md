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

### 3. works — 작품명 오기 행 2건 (병합 후 남은 빈 껍데기)

| 항목 | 값 |
|---|---|
| 대상 | `works` 행 2건 — `메스를든사냥꾼` `b255563e-18fd-4136-9794-f6b8fbebd7ac` · `살롱드홈즈` `0606a437-f3fc-4b05-9e27-09c200219442` |
| 이유 | 같은 작품이 **띄어쓰기 차이로 두 행**으로 갈려 있었다. 오기 행에 붙은 클립은 작품명이 laeebly 표기와 달라 **권리 조회(식별코드·가이드·지오블락 판정)가 통째로 실패**한다 |
| 처리 완료분 | 2026-07-26 클립을 정본 행으로 이동(UPDATE): 메스를 든 사냥꾼 40→**65** · 살롱 드 홈즈 157→**215** (총 83행). `recall_benchmark` 는 0행이라 이동 대상 없음 |
| 남은 일 | 클립이 0인 **빈 행 2건 삭제** (자동 DELETE 차단됨 → 수동) |
| 상태 | ⏳ 미처리 |

> ⚠️ 오기 행의 제목만 고치는 rename 은 **불가** — 정본 행이 이미 그 제목을 쓰고 있어
> `works_title_uniq UNIQUE (title, content_type)` 에 걸린다. 그래서 병합 방식으로 처리했다.

확인(둘 다 clips=0 이어야 함):
```sql
SELECT w.id, w.title, count(c.id) AS clips
FROM works w LEFT JOIN clips c ON c.work_id = w.id
WHERE w.id IN ('b255563e-18fd-4136-9794-f6b8fbebd7ac',
               '0606a437-f3fc-4b05-9e27-09c200219442')
GROUP BY w.id, w.title;
-- 기대: 메스를든사냥꾼 | 0 · 살롱드홈즈 | 0
```

삭제:
```sql
BEGIN;
DELETE FROM works WHERE id = 'b255563e-18fd-4136-9794-f6b8fbebd7ac';  -- 메스를든사냥꾼, 1행
DELETE FROM works WHERE id = '0606a437-f3fc-4b05-9e27-09c200219442';  -- 살롱드홈즈, 1행
COMMIT;
```

검증:
```sql
SELECT title, count(*) FROM works
WHERE title IN ('메스를 든 사냥꾼','메스를든사냥꾼','살롱 드 홈즈','살롱드홈즈')
GROUP BY title;
-- 기대: '메스를 든 사냥꾼' 1행 · '살롱 드 홈즈' 1행 (오기 표기는 사라짐)
```

---

### 4. 언니네 산지직송 in 칼라페 — 소스가 칼라페가 아닌 클립 (너굴안방)

| 항목 | 값 |
|---|---|
| clip_id | `db2ff1a5-306a-4c6e-82d3-caf8216de2e0` (`episode='shorts_1'`, dur 49.7) |
| 작품 | 언니네 산지직송 in 칼라페 (너굴안방) |
| 쇼츠 제목 | `식빵 잡으려 기계에 입부터 넣으면? 여배우 은진의 야생성 폭발한 순간` |
| 이유 | **소스 영상이 칼라페 시즌이 아니다.** 수동 다운로드분 `~/Downloads/sources/sanjik_ep1/source.mp4`(671초)로 만들었는데, 자막에 칼라페 출연진(염정아·박준면·김혜윤·덱스)·'칼라페'·'보홀'·'필리핀'이 **0건**이고 "진영아"·"시즌 2 때 게스트로" 등 다른 시즌 대사만 나온다. 안은진은 이전 시즌 멤버라 제목의 '은진'은 그 영상 기준으론 맞는 이름 — 틀린 건 이름이 아니라 소스다 |
| YouTube | `pTdd4lwFTpA` (unlisted, 사람이 Studio에서 별도 삭제) |
| 자식 행 | `judge_runs` 1 · `clip_metadata` 1 — 나머지 5개 테이블은 0건 확인(2026-07-26) |
| 후속 | scene_loop 상태(`results/scene_loop_state.json`)의 칼라페 EP1 장면에서 제거 완료. 루프는 EP1 소스로 `ECbzm_ha64k`(827초, 제목에 `#언니네산지직송in칼라페 EP.1`)를 고른다 |
| 상태 | ⏳ 미처리 (자동 DELETE 차단됨 → 수동) |

> ⚠️ 교훈: 소스를 수동으로 받아 쓰면 **작품·회차가 맞는지 검증하는 단계가 없다.** scene_loop 의 유튜브 소스는
> 제목 해시태그 정규식으로 작품을 한정하므로 이 사고가 구조적으로 막힌다.

확인:
```sql
SELECT w.title, c.id, c.episode, c.duration_sec, c.video_external_id
FROM clips c JOIN works w ON w.id = c.work_id
WHERE c.id = 'db2ff1a5-306a-4c6e-82d3-caf8216de2e0';
-- 기대: 언니네 산지직송 in 칼라페 | db2ff1a5… | shorts_1 | 49.7 | pTdd4lwFTpA
DELETE FROM judge_runs    WHERE clip_id = 'db2ff1a5-306a-4c6e-82d3-caf8216de2e0';  -- 1행
DELETE FROM clip_metadata WHERE clip_id = 'db2ff1a5-306a-4c6e-82d3-caf8216de2e0';  -- 1행
DELETE FROM clips         WHERE id      = 'db2ff1a5-306a-4c6e-82d3-caf8216de2e0';  -- 1행
COMMIT;
```

---

### 5. 언니네 산지직송 in 칼라페 — 같은 갯벌 장면 중복본 (너굴안방, 비교 후 폐기분)

| 항목 | 값 |
|---|---|
| clip_id | `54da4958-3741-4187-8d8c-be7d1167cb41` (`episode='shorts_1'`, dur 49.7) |
| 작품 | 언니네 산지직송 in 칼라페 (너굴안방) |
| 쇼츠 제목 | `갯벌에서 다리만 잡다가 거대 알리망오 잡은 박준면` |
| 이유 | 채택본과 **같은 갯벌 장면**(이 클립 504.2~553.9초 / 채택본 518.2~567.9초, 36초 겹침 — IoU 0.56 으로 루프 중복 임계값 0.5 초과). 둘 중 하나만 남겨야 해서 **리서치 없이 생성된 이쪽을 폐기**. 사람이 두 영상을 비교해 결정(2026-07-26) |
| YouTube | `qqdYxnsiibA` (private, 사람이 Studio에서 별도 삭제) |
| 채택본 | clip `aec87cfd-7359-4234-9073-bdae80a245a5` → `6K9CS3ui1Ro` (`갯벌 속에서 발견한 압도적 크기의 괴물 게`, 작품 리서치 켜고 생성) |
| 자식 행 | `judge_runs` 1 · `clip_metadata` 1 — 나머지 5개 테이블은 0건 확인(2026-07-26) |
| 산출물 | `~/ves/ai-video/rejected/너굴안방/ep01_7e_인물명오류_20260726/` (스캔 경로 밖으로 옮겨 보관) |
| 상태 | ⏳ 미처리 (자동 DELETE 차단됨 → 수동) |

확인:
```sql
SELECT w.title, c.id, c.episode, c.duration_sec, c.video_external_id
FROM clips c JOIN works w ON w.id = c.work_id
WHERE c.id = '54da4958-3741-4187-8d8c-be7d1167cb41';
-- 기대: 언니네 산지직송 in 칼라페 | 54da4958… | shorts_1 | 49.7 | qqdYxnsiibA
```

삭제:
```sql
BEGIN;
DELETE FROM judge_runs    WHERE clip_id = '54da4958-3741-4187-8d8c-be7d1167cb41';  -- 1행
DELETE FROM clip_metadata WHERE clip_id = '54da4958-3741-4187-8d8c-be7d1167cb41';  -- 1행
DELETE FROM clips         WHERE id      = '54da4958-3741-4187-8d8c-be7d1167cb41';  -- 1행
COMMIT;
```

검증(4번·5번 함께 처리 후):
```sql
SELECT c.id, c.episode, c.duration_sec, c.video_external_id
FROM clips c JOIN works w ON w.id = c.work_id
WHERE w.title = '언니네 산지직송 in 칼라페' AND c.episode IS NOT NULL
ORDER BY c.created_at;
-- 기대: db2ff1a5 · 54da4958 가 사라지고 아래 4행이 남는다.
--   cfe68392… | shorts_1 | 47.77 | L-Yv8zAsJOw  (EP5)
--   269ac525… | shorts_1 | 49.7  | PIiIfEIHaVg  (EP5)
--   0cf7f7c5… | shorts_1 | 49.7  | Ea334qKq6Xg  (EP5)
--   aec87cfd… | shorts_1 | 49.7  | 6K9CS3ui1Ro  (EP1 채택본)
-- ※ episode IS NULL 인 14행은 scene_loop 이전 유입분이라 이 대조 대상이 아니다.
```

---

### 6. 샤먼: 미신전 — 대사자막 켜진 채 생성된 scene_loop 첫 산출물

| 항목 | 값 |
|---|---|
| clip_id | `6f18e56c-7a02-4d80-bb8d-49eeec25fb06` |
| 작품/채널 | 샤먼: 미신전 / 여운 보관소 (`episode='shorts_1'`, 소스 구간 230.6~450.4초) |
| 이유 | `config/scene_loop.json` 의 `gen_flags` 에 **`--no-subtitles` 가 빠져 있어** 파이프라인 대사자막이 켜진 채 생성됐다. 원본(티빙 클립마스터)에 이미 자막이 하드번인돼 있어 **같은 대사가 이중으로 겹친다**(2번 항목과 동일한 결함의 재발) |
| YouTube | `xXuCd6b5Fow` (사람이 Studio 에서 별도 삭제) |
| 대체 | 2026-07-27 `--no-subtitles` 적용 후 같은 회차에서 재생성 |
| 재발 방지 | `gen_flags` 에 `--no-subtitles` 고정 + `_gen_flags_note` 로 이유 명시. 예약 작업 프롬프트에도 "생성 후 `shorts.filter.txt` 에 `subtitles.ass` 가 있으면 보고" 추가 |
| 상태 | ⏳ 미처리 (자동 DELETE 차단됨 → 수동) |

### 7. 놀라운 토요일 — Gemini 폴백으로 제목·구성이 망가진 산출물

| 항목 | 값 |
|---|---|
| clip_id | `30535485-3c91-4b9f-a58d-56dd764c84e7` |
| 작품/채널 | 놀라운 토요일 / 숏나우저 (`episode='shorts_1'`, EP410) |
| 이유 | **Gemini 스토리 구성 실패 → 폴백 경로**. `checkpoint_story.json` 에 근거가 남아 있다: `selection_reason='폴백: Gemini 응답 실패 — 상위 moment 자동 조합'`, `score=0.5`. 결과로 ① 제목이 `작품명` + `hook 설명 앞 20자` 로 조립돼 **문장 중간에서 잘림**(`놀라운 토요일 / 김동현이 바닥에 등을 대고 누워 양발`) ② 구간이 41~993초로 **소스 전체에 퍼짐**(hook 45초·build 546/780초·payoff 960초를 기계적으로 이어붙임) ③ 대사자막도 켜짐(4번과 동일) |
| YouTube | `6EZWuOz8biE` (사람이 Studio 에서 별도 삭제) |
| 대체 | 2026-07-27 재생성 |
| 상태 | ⏳ 미처리 (자동 DELETE 차단됨 → 수동) |

> ⚠️ **judge 는 폴백을 못 거른다** — 이 클립은 quality 0.85 로 통과했다. judge 는 안전(환각·깨짐) 전용이라
> "정상 경로로 만들어졌는가"는 보지 않는다. 그래서 예약 작업 프롬프트에 `checkpoint_story.json` 의
> `selection_reason`/`topic_reason` 에 '폴백'·'실패' 가 있으면 **발행하지 말고 보고**하는 가드를 넣었다.
>
> ⚠️ 폴백 구간이 소스 전체를 덮으면 **다음 생성이 전부 중복 판정**돼 그 회차가 막힌다. 재생성 전에
> `results/scene_loop_state.json` 에서 해당 장면을 빼고 산출물을 `outputs/` 밖으로 치워야 한다
> (2026-07-27 처리분은 `ai-video/_quarantine/20260727_bad_scenes/` 로 이동).
>
> ⚠️ **폴백은 재발한다.** 2026-07-27 재생성에서 3채널 중 1채널(이거보고자, `놀라운_토요일_7f`)이 또
> 폴백을 탔다. 같은 소스·같은 회차인데 숏나우저는 정상(score 0.95)이고 이거보고자만 실패한 것으로 보아
> **간헐적 Gemini 응답 실패**로 보인다. 이건 발행 전에 걸러 DB 에 안 들어갔다(아래 표 참조).
> 재발 시 판정 기준: `checkpoint_story.json` → `raw_response.selection_reason` 에 '폴백'/'실패',
> `storylines[0].score == 0.5`, `edit_plan.timeline` 구간이 소스 전체로 퍼짐.

확인(6·7 공통):
```sql
SELECT ch.name, w.title, cl.id, cl.episode, cl.duration_sec, cl.video_external_id
FROM clips cl
JOIN clip_metadata m ON m.clip_id = cl.id
LEFT JOIN works w ON w.id = cl.work_id
LEFT JOIN channels ch ON ch.id = cl.channel_id
WHERE cl.id IN ('6f18e56c-7a02-4d80-bb8d-49eeec25fb06',
                '30535485-3c91-4b9f-a58d-56dd764c84e7');
-- 기대: 여운 보관소 | 샤먼: 미신전 | 6f18e56c… | xXuCd6b5Fow
--       숏나우저   | 놀라운 토요일 | 30535485… | 6EZWuOz8biE
DELETE FROM judge_runs    WHERE clip_id IN ('6f18e56c-7a02-4d80-bb8d-49eeec25fb06',
                                            '30535485-3c91-4b9f-a58d-56dd764c84e7');  -- 2행
DELETE FROM clip_metadata WHERE clip_id IN ('6f18e56c-7a02-4d80-bb8d-49eeec25fb06',
                                            '30535485-3c91-4b9f-a58d-56dd764c84e7');  -- 2행
DELETE FROM clips         WHERE id      IN ('6f18e56c-7a02-4d80-bb8d-49eeec25fb06',
                                            '30535485-3c91-4b9f-a58d-56dd764c84e7');  -- 2행
COMMIT;
```

검증:
```sql
SELECT count(*) FROM clips
WHERE id IN ('6f18e56c-7a02-4d80-bb8d-49eeec25fb06','30535485-3c91-4b9f-a58d-56dd764c84e7');
-- 기대: 0
```

**대체본**(정상 경로로 재생성, 2026-07-27 발행 — 삭제 대상 아님):

| 대체 대상 | 새 clip_id | YouTube | story score | judge quality | 비고 |
|---|---|---|---|---|---|
| `6f18e56c`(샤먼) | `2a4d9a55-c4e5-48f6-9ddd-1b4fead1fc2f` | `XAcBH4XZqFU` | 0.92 | 0.825 | 861.3~911.0초, 자막 TTS 만 |
| `30535485`(놀토) | `b378e6c3-1e2e-4e6a-bd96-9c1284404fb8` | `IbWcgnLUNgQ` | 0.95 | 0.725 | 623.4~673.1초, 자막 TTS 만 |

> story score 는 스토리 구성 단계의 자체 점수(폴백이면 0.5 고정)이고, judge quality 는 발행 게이트가
> 보는 안전 판정이다. **서로 다른 값이니 섞어 읽지 말 것.**

**DB 에 안 들어간 폐기분**(인제스트 전에 걸러냄 — 원장 등재 불필요, 기록용):
`놀라운_토요일_7f`(이거보고자 EP410) — 폴백. `_quarantine/20260727_bad_scenes/이거보고자_ep410_fallback/````

삭제:
```sql
BEGIN;

---

### 8. 커리어데이 — 운영자 폐기분 2편 (커리어데이 숏츠 EP1)

| 항목 | 값 |
|---|---|
| clip_id | `85ff8a6e-0a9a-47fd-a3ac-cb33a6750183` (`커리어데이_b9`) · `31f6c87e-e76d-4cb6-a1f0-fcc7ef455712` (`커리어데이_43`) |
| 작품/채널 | 커리어데이 / 커리어데이 숏츠 (EP1, 소스 `bhDg5442l2g`) |
| 이유 | **운영자 판단으로 폐기**(2026-07-30). 품질·안전 결함이 아니라 쓰지 않기로 한 것이다. 2026-07-27 에 unlisted 로 업로드됐다가 운영자가 private 으로 내렸다 |
| YouTube | `0QtpTJUCuFw` · `K4CEPC8LvEs` — 둘 다 현재 **private**. 사람이 Studio 에서 별도 삭제 |
| 산출물 | `~/ves/ai-video/rejected/careerday_ep01_커리어데이_b9_try1_20260727_045304/` · `…_커리어데이_43_try1_20260727_045901/` 로 이동 완료(루프 스캔 경로 밖) |
| 상태 | ⏳ 미처리 (자동 DELETE 차단됨 → 수동) |

> ✅ `results/scene_loop_state.json` 에서 두 장면을 **이미 제거**했다(2026-07-30). 안 빼면 루프가 그
> 구간을 '이미 만든 장면' 으로 보고 영영 피해 다닌다(§6-4). 남은 EP1 장면은 `커리어데이_5d` 1건이다.
>
> ⚠️ **private 은 루프가 못 본다.** 회차 완료 카운트(`count_mode='public'`)는 공개 API 키로
> `videos.list` 를 부르는데 private 은 결과에 아예 안 나온다 — unlisted 는 '미공개 대기' 로 세지만
> private 은 없는 것과 같다. 폐기분을 private 으로 두는 것 자체는 안전하지만, **폐기했으면 상태
> 파일에서도 빼야** 그 구간이 다시 열린다.

확인:
```sql
SELECT ch.name, w.title, cl.id, cl.episode, cl.duration_sec, cl.video_external_id
FROM clips cl
LEFT JOIN works w ON w.id = cl.work_id
LEFT JOIN channels ch ON ch.id = cl.channel_id
WHERE cl.id IN ('85ff8a6e-0a9a-47fd-a3ac-cb33a6750183',
                '31f6c87e-e76d-4cb6-a1f0-fcc7ef455712');
-- 기대: 커리어데이 숏츠 | 커리어데이 | … | 0QtpTJUCuFw / K4CEPC8LvEs (2건)
```

---

### 9. 참교육 — 운영자 폐기분 1편 (쇼츠션샤인 EP1, 유튜브 영상은 이미 삭제됨)

| 항목 | 값 |
|---|---|
| clip_id | `d5498d4a-1ae4-46c2-bb3f-398455c00deb` (`참교육_d4`) |
| 작품/채널 | 참교육 / 쇼츠션샤인 (EP1, 소스 구간 1317.1~2214.4초, dur 49.8) |
| 이유 | **운영자 판단으로 폐기**(2026-08-05 의도 확인). 2026-08-04 unlisted 발행분 12건 검수 중 폐기 결정 |
| YouTube | `wlGkFBRu53I` — **운영자가 Studio 에서 이미 삭제**(2026-08-05 채널 OAuth 조회에서 '응답에 없음' 확인). 다른 항목과 달리 유튜브 쪽은 처리 완료 |
| 자식 행 | `judge_runs` 1 · `clip_metadata` 1 — `clip_features`/`clip_performance`/`golden_human_labels`/`improvement_directives`/`reward_scores`/`review_decisions` 는 0건 확인(2026-08-05) |
| 상태 | ⏳ 미처리 (자동 DELETE 차단됨 → 수동) |

> ✅ `results/scene_loop_state.json` 의 장면은 **일부러 남겨뒀다**(8번과 반대) — 남기면 루프가
> 그 구간을 피해 다니고(삭제=반려 분류, 대기 점유 없음), 같은 장면이 다시 만들어지지 않는다.
> 운영자가 내용 자체를 버린 폐기라 구간 차단이 맞다. 같은 구간을 다시 만들고 싶어지면 §6-4 절차로
> 상태에서 빼면 된다. 산출물 job_dir 도 같은 이유로 스캔 경로에 그대로 둔다.
>
> ⚠️ `results/scene_publish_state.json` 의 `참교육_d4` 기록(stage=published)은 **지우지 말 것** —
> DB clips 행까지 지운 뒤에 이 기록마저 없으면 scene_publish_loop 가 미발행 장면으로 보고
> 재업로드한다(발행 이력이 이중 방어선이다).

확인:
```sql
SELECT w.title, c.id, c.duration_sec, c.video_external_id
FROM clips c LEFT JOIN works w ON w.id = c.work_id
WHERE c.id = 'd5498d4a-1ae4-46c2-bb3f-398455c00deb';
-- 기대: 참교육 | d5498d4a… | 49.84 | wlGkFBRu53I (유튜브에선 이미 삭제된 링크)
```

삭제:
```sql
BEGIN;
DELETE FROM judge_runs    WHERE clip_id = 'd5498d4a-1ae4-46c2-bb3f-398455c00deb';  -- 1행
DELETE FROM clip_metadata WHERE clip_id = 'd5498d4a-1ae4-46c2-bb3f-398455c00deb';  -- 1행
DELETE FROM clips         WHERE id      = 'd5498d4a-1ae4-46c2-bb3f-398455c00deb';  -- 1행
COMMIT;
```

검증:
```sql
SELECT c.id, c.video_external_id FROM clips c
WHERE c.video_external_id = 'wlGkFBRu53I';
-- 기대: 0행
```

---

## 완료 항목

_(없음 — 처리 후 여기로 이동)_

---

## 판단 대기: works 중 laeebly 에 대응 작품이 없는 제목 (2026-07-26 조사)

작품명이 laeebly `licensed_video.title` 과 일치해야 권리 조회(식별코드·가이드·지오블락)가 된다.
아래 17건은 **오타가 아니라 laeebly 에 해당 제목이 아예 없는** 경우라 기계적으로 못 고친다 —
작품별로 "같은 작품인가/다른 작품인가"를 사람이 판단해야 한다. **삭제 대상 아님**(판단 후 결정).

| works.title | clips | 메모 |
|---|---|---|
| `내편하자` | **499** | laeebly 엔 `믿고 말해보는 편, 내편하자 4` 존재 — 같은 작품인지 다른 시즌인지 확인 필요. 규모가 커서 잘못 묶으면 피해 큼 |
| `<파인 : 촌뜨기들>` | 6 | laeebly 엔 `파인: 촌뜨기들` — 꺾쇠·공백 차이로 보이나 단정 못 함 |
| `클라이맥스` | 0 | laeebly 엔 `클라이맥스 (브랜드 채널용)` / `(일반 채널용)` 둘로 나뉨 |
| `초고속 결혼 후 열애` | 0 | laeebly 엔 `초고속 결혼 후 열애중` |
| `''`(빈 문자열) | 0 | 제목 없는 행 — 유입 경로 확인 필요 |
| `골목 끝 극장` · `달빛서점` · `도시의 온도` · `마지막 열차의 노래` · `밤을 건너는 탐정` · `비밀의 정원사` · `스튜디오 404` · `연필로 쓴 미래` · `오렌지빛 약속` · `유리도시의 연인` · `청춘은 재생되지 않는다` · `하이퍼리얼 로맨스` | 각 1~2 | laeebly 미등재. 비계약 작품이거나 실험분일 가능성 |

재조사 SQL:
```sql
-- ves(파이프라인 DB)에서 실행하되 laeebly 제목 목록과 대조해야 하므로 스크립트로 처리 권장
SELECT w.title, count(c.id) FROM works w LEFT JOIN clips c ON c.work_id=w.id
GROUP BY w.title ORDER BY count(c.id) DESC;
```

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
