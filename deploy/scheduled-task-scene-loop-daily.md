---
name: scene-loop-daily
description: 지난밤 launchd 가 돌린 scene_loop 생성 결과를 읽고 보고한다 — 생성은 하지 않는다
---

`/scene-loop-daily` 를 실행한다.

절차는 레포 정본에 있다 — `.claude/skills/scene-loop-daily/SKILL.md`.
**이 파일에 절차를 복사해 두지 않는다. 머신 이름·담당 채널도 적지 않는다.**
손편집이 머신마다 배정과 동작이 어긋나는 원인이었다. 담당 채널은 `config/assignments.json`
정본에서 루프가 스스로 찾고, 구성은 `SCENE_LOOP_OPERATIONS.md §5` 가 정본이다.

표준 구성(6대 동일): 생성 = launchd `com.rhoonart.scene-loop` / 보고 = 이 예약작업.
**시각은 머신마다 다르다** — `config/assignments.json` 의 `schedule.at` 이 정본이고,
같은 `gemini_key` 를 쓰는 짝과 겹치면 쿼터를 잡아먹어 생성이 실패한다. 보고는 생성 + 6시간.

⛔ **생성을 직접 띄우지 않는다.** 로그가 비어 있거나 실패했어도 대신 돌리지 말고 보고만 한다.
