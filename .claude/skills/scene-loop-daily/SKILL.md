---
name: scene-loop-daily
description: 회차 진행형 쇼츠 생성 루프(scene_loop)를 하루 1회 돌리고 결과를 보고한다. 매일 예약 실행으로 부르거나, 사람이 수동으로 한 번 돌릴 때 쓴다.
---

# scene_loop 야간 실행

회차 진행형 쇼츠 생성 루프를 1회 돌리고 결과를 보고하는 작업이다.

> ★ **담당 채널을 이 문서에 적지 않는다.** 배정 정본은 `config/assignments.json` 이고 루프가
> 스스로 이 머신의 담당분을 찾는다. 예전에는 이 프롬프트 마지막 줄에 채널명을 손으로 적었는데,
> 그 손편집이 머신마다 배정이 어긋나는 원인이었다.

## 배경

`~/ves/ai-improvement-edit-video` 의 `scripts/scene_loop.py` 는 채널마다 소스에서 회차를
오름차순으로 소비하며 회차당 서로 다른 장면 3개를 채우는 생성 루프다.

- 회차 완료 판정은 유튜브에 **공개(public)** 된 장면만 카운트한다. unlisted 는 안 센다
  → 사람이 검수하고 공개해야 다음 회차로 넘어간다. **이게 의도된 브레이크다.**
- 1회 실행에서 채널당 1장면만 생성한다(폭주 방지).
- 미공개 대기가 3개 쌓이면 그 회차 생성을 멈추고 사람의 공개를 기다린다.
- 생성 결과가 기존 장면과 구간이 겹치면 최대 2회 재생성한다. 중복 판정은 **채널 단위로 닫혀
  있다** — 한 작품을 여러 채널이 써도 서로 간섭하지 않는다.
- 생성만 한다. 인제스트·judge·발행은 이 루프에 포함되지 않는다.

## 절차

1) 지난 실행 결과 확인

```bash
tail -60 ~/ves/ai-improvement-edit-video/results/scene_loop.log
```

2) 오늘 계획 확인 (생성 없음). 배정·작품 카드 검증이 먼저 돈다

```bash
cd ~/ves/ai-improvement-edit-video && .venv/bin/python scripts/check_assignments.py
cd ~/ves/ai-improvement-edit-video && .venv/bin/python scripts/scene_loop.py --dry-run
```

※ `.env` 를 손으로 불러올 필요 없다 — `scene_loop.py` 가 `load_env()` 로 스스로 읽는다.
  명령이 단순해야 무인 실행용 권한 허용이 좁고 정확해진다(`.claude/settings.json`).

⛔ 가 하나라도 있으면 **생성하지 말고 그 내용만 보고하고 끝낸다.** 배정이 틀린 채로 생성하면
남의 채널 것을 만들거나 권리 범위를 벗어난다.

3) 실제 실행을 백그라운드로 띄운다 (생성은 편당 수십 분~90분이라 동기 실행 불가)

```bash
cd ~/ves/ai-improvement-edit-video && nohup ./scripts/scene_loop_run.sh > /dev/null 2>&1 &
```

4) 30초 뒤 로그 꼬리를 읽어 **정상 시작만** 확인하고 종료한다. 완료까지 기다리지 않는다.

## 보고 형식 (짧게)

- 지난 실행 결과 요약
- 오늘 계획 — 채널별 EP·공개 n/3
- 이번에 띄운 생성 작업
- **사람이 할 일** — 미공개 대기로 멈춘 채널, 소스가 없어 대기 중인 회차, 검증 경고

## 주의

- 실패해도 재시도하지 않는다. 사유만 보고한다(생성 비용·Gemini 지출 한도).
- `scene_loop.py` 를 포그라운드로 돌리지 않는다(Bash 도구 시간 상한).
- `config/assignments.json`·`works.json`·`results/scene_loop_state.json` 을 임의로 고치지 않는다.
- 생성 결과가 이상해도(인물명 오류 등) 스스로 판단해 재생성하지 않는다 — 보고하고 사람이 정한다.
