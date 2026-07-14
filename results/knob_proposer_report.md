# 노브 후보 리포트 (제안기 v0 — good vs bad 대조)

> K1·K3: **후보만** 제시. 채택은 사람 검토 → 1노브 A/B → CI 게이트(K4). config 직접 수정 아님.

대상 클러스터(good/bad ≥15): 예능×에피소드완결(237/197), 드라마×연속서사(147/123), 영화×단편완결(36/38), 논픽션×비서사(27/34), 예능×비서사(19/17)


| # | 우선 | 클러스터 | 피처 | δ(효과) | n(g/b) | 방향 | 조절면 | 트랙 |
|---|---|---|---|---|---|---|---|---|
| 1 | 0.399 | 예능×비서사 | loudness_dynamics | -0.53(large) | 19/17 | good 클립이 loudness_dynamics가 낮음 | 게인 3종·loudness LUFS | pair |
| 2 | 0.396 | 예능×비서사 | action_density | +0.53(large) | 19/17 | good 클립이 action_density가 높음 | 스토리 프롬프트 (L0) | cohort |
| 3 | 0.373 | 예능×비서사 | cut_rhythm_var | +0.49(large) | 19/17 | good 클립이 cut_rhythm_var가 높음 | moment 선택 (컷 리듬 분산) | cohort |
| 4 | 0.355 | 논픽션×비서사 | subtitle_density ⚠길이 | +0.37(medium) | 27/33 | good 클립이 subtitle_density가 높음 | 자막 프리셋·max_chars/lines | pair |
| 5 | 0.342 | 영화×단편완결 | hook_timing_sec | -0.34(medium) | 36/37 | good 클립이 hook_timing_sec가 낮음 | 훅 지시문·moment 선택 | cohort |
| 6 | 0.277 | 예능×비서사 | dialogue_density | -0.37(medium) | 19/17 | good 클립이 dialogue_density가 낮음 | 스토리 프롬프트 (L0) | cohort |

⚠길이 = duration 과 상관 — apv 정규화 artifact 방어 위해 판정 시 절대 시청시간 가드레일 필수(§3-2). L0=프롬프트·L1=config. 방향은 '좋은 클립이 어느 쪽인가'.
