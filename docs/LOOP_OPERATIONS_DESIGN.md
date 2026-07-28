# 루프-루틴 운영 체계 설계 (v1)

> **2026-07-28 확정.** `scene_loop` 를 여러 컴퓨터에서 굴릴 때 배정·작품 제약·정책이 흩어져
> 생기던 조용한 사고를 구조로 막는다. 운영 절차는 `SCENE_LOOP_OPERATIONS.md`(정본)이고,
> 이 문서는 **왜 그렇게 만들었는지**와 폐기한 대안을 남긴다.
> 이 문서의 `file:line` 은 2026-07-28 기준 코드 실측.

---

## 0. 확정 결정

| # | 결정 | 근거 |
|---|---|---|
| D1 | 배정 정본은 공유 파일 하나 (`config/assignments.json`) | 머신 간 락이 없다. 상태 파일이 머신별이라 서로를 볼 수 없으므로, **공유 파일에서 사전 차단**이 유일한 방어선 |
| D2 | 작품 카드(`config/works.json`)는 소스·회차 규칙 + 제약 플래그까지. `work_publish_notice.json` 은 흡수하지 않음 | 흡수하면 `publish_youtube.py` 를 고쳐야 하는데 그 파일은 권리 판정 3종이 걸려 있어 회귀 위험이 크다 |
| D3 | 이 맥에서 먼저 적용해 실기동 검증 후 확산 | 하위호환이 전제 — 다른 머신은 pull 해도 예전 방식으로 그대로 돌아야 한다 |
| D4 | scene_loop 코드를 main 에 병합 | docs 브랜치에만 있어 머신마다 파일 4개를 손으로 얹는 구조가 로컬 수정 드리프트를 만들었다 |

---

## 1. 무엇이 문제였나 (실측)

| # | 문제 | 근거 |
|---|---|---|
| 1 | 작품명이 **4곳에서 4가지 매칭 규칙**으로 쓰임 | `channels.json.works[]`(정확) · `scene_loop.json.work_title`(정확) · `work_publish_notice.json` 키(정규화 없는 dict 조회, `publish_youtube.py:154`) · laeebly `licensed_video.title`(SQL 완전일치, `publish_youtube.py:229`). 한 글자만 달라도 권리 조회가 통째로 실패하고 **경고만 뜬 채 발행**된다 |
| 2 | 정규화 유틸이 3개인데 **권리 경로는 아무것도 안 씀** | `factory/cluster._norm_title:50` 은 `시즌\d+` 를 지운다 → SNL 시즌7/8 구분이 사라진다. 어느 라이선스 행을 쓸지 가르는 구분이라 권리 경로에 부적합 |
| 3 | 작품 속성이 머신별 파일에 있음 | 같은 작품을 다른 머신이 맡으면 손으로 다시 옮겨 적어야 하고 어긋난다. 실제로 도깨비 하한만 600, 나머지 500 으로 갈렸다 |
| 4 | 소스 범위가 **읽을 수 있는 플래그로 없음** | 도깨비는 "해당 플레이리스트 영상만 사용 가능"인데 URL 모양으로 추론해야 했다. `_guide` 메모는 아무도 안 읽는다 |
| 5 | 머신 간 채널 중복 배정을 기계적으로 못 막음 | 상태가 머신별 |
| 6 | 자막 정책이 전역 플래그 | 자막 있는 작품과 없는 작품이 한 설정에 공존 불가(`scene_loop.json` `gen_flags`) |
| 7 | `per_run_scenes_per_channel` 이 죽은 노브 | 설정에 있고 `_doc` 에도 적혀 있지만 아무도 읽지 않는다(1이 코드 고정) |

---

## 2. 설계 — 세 개의 핵심 선택

### 2-1. 소스 범위를 `source.type` 한 필드에 인코딩

`youtube_playlist` / `youtube_channel` / `local`. 범위를 별도 필드(`source_scope`)로 두는 안을
버렸다 — **두 필드는 어긋날 수 있고, 어긋난 쪽이 권리 범위면 사고다.** 한 필드면 자기 자신과
어긋날 수 없다.

권리 범위는 **3층으로** 지킨다:

| 층 | 무엇 |
|---|---|
| 선언 | `source.type` 이 범위 그 자체 |
| 모양 | 검증이 type ↔ url 모양 불일치를 ⛔ (`check_assignments.url_matches_type`) |
| 런타임 | yt-dlp 호출 **전에** `scene_loop.assert_source_scope` 가 한 번 더 본다 |

여기에 기존 `merge_index` 의 "새 목록에 없는 id 는 버린다"가 더해져, 플레이리스트에서 빠진
영상이 캐시에 남아 소스로 쓰이는 일도 막힌다.

### 2-2. 해석 계층은 예전 채널 dict 모양 그대로 낸다

`channel_registry.effective_channel_configs` 가 `channel`·`work_title`·`source_type`·
`source_url`·`title_episode_regex`·`min_source_duration_sec`·`gen_flags` 를 만든다.

**왜 새 모양을 만들지 않았나**: `channel_plan`·`discover_episodes_for`·`index_episodes`·
`rendered_scenes`·`build_cmd` 를 한 줄도 고치지 않기 위해서다. 레거시 모드와 신규 모드가 하류
코드 경로를 **공유**하므로 동작이 갈릴 여지가 없다. 경로가 갈리면 동작도 갈린다.

`scene_loop.py` 실제 변경은 3곳뿐 — `main()` 모드 선택 · `process_channel` 의 작품별 플래그 ·
생성 전 권리 범위 assert.

### 2-3. 모드는 파일명이 아니라 "이 머신이 배정 정본에 있는가"로 갈린다

| 조건 | 모드 |
|---|---|
| 머신 식별 성공 AND 담당 채널 작품에 카드 존재 | resolver |
| 위가 아니고 `scene_loop.json` 에 `channels` 존재 | legacy (예전과 100% 동일) |
| 둘 다 없음 | ⛔ 종료 |
| `--machine`/`SCENE_LOOP_MACHINE` 을 명시했는데 배정에 없음 | ⛔ 종료 (**폴백하지 않는다**) |

마지막 줄이 중요하다. 명시한 의도가 있는데 조용히 다른 채널 집합으로 도는 것이 가장 위험하다.

🛑 식별 실패에서 "전 채널"로 폴백하는 선택지는 처음부터 배제했다 — 모르는 채로 돌면 남의 채널까지
생성한다.

---

## 3. 폐기한 대안

| 대안 | 왜 버렸나 |
|---|---|
| 배정을 머신별 파일에 두고 검증만 강화 | 머신 정보가 깃에 없으면 **다른 머신의 배정을 알 수 없어** 중복을 기계적으로 못 막는다. D1 의 목적 자체가 그것 |
| 전역 정책을 `assignments.json._defaults` 에 합치기 | 배정은 주 단위, 정책은 드물게 바뀐다. 한 파일에 두면 정책 튜닝이 소유권 파일을 건드려 리뷰가 흐려진다 |
| `work_publish_notice.json` 을 작품 카드로 흡수 | `publish_youtube.py` 는 권리 판정 3종(지오블락·식별코드·표기)이 걸린 파일이다. 이번 변경에서 손대지 않는 것이 안전 이득이 더 크다 |
| 미등재 작품(참교육·커리어데이) 채널 배정 해제 | 채널을 놀리는 결정이라 사업 판단이다. 카드에 `rights_lookup:"none"` 으로 **선언**하고 검증이 상시 보고하게 했다 — 공백을 grep 가능하게 남기고 조용히 초록이 되지 않게 |
| `autogen.build_gen_cmd` 와 `scene_loop.build_cmd` 통합 | autogen 은 gen_queue·A/B 라운드 경로다. 합치면 A/B 라운드 config 가 일상 루프 정책에 묶여 주입 seam 이 무력화된다. **합치는 시점 = loudness A/B 라운드 착수 시** |
| `per_run_scenes_per_channel` 을 실제로 구현 | 하룻밤 생성비가 채널당 68분×N 로 늘어 위험을 새로 만든다. 정책 파일에서 아예 뺐고, 검증의 미지 키 거부가 부활을 막는다 |

---

## 4. 안전 장치가 걸리는 지점

생성·발행까지 가는 길에 배정 오류가 걸러지는 곳:

| # | 지점 | 무엇을 막나 |
|---|---|---|
| 1 | 머신 식별 실패 → ⛔ 종료 | 남의 채널 생성 |
| 2 | 배정 채널이 `channels.json` 에 없음 → ⛔ | 오타 채널 |
| 3 | 작품 카드 없음 → ⛔ | 설정 누락 상태로 도는 것 |
| 4 | **러너·스킬 0단계 검증** → ⛔ 면 생성 미시작 | 위 전부 + 권리 범위·지오블락 |
| 5 | 생성 직전 `assert_source_scope` | 레거시 경로로 들어온 범위 위반 |
| 6 | 발행 시 채널 토큰 하드매칭 + `geoblock_ok` | 오채널 발행 · 지오블락 |

⚠️ **지오블락은 카드가 틀려도 발행을 풀어줄 수 없다.** 발행 게이트(`publish_youtube.geoblock_ok`)는
끝까지 laeebly `guide` 로 독립 판정한다. 카드의 `geoblock_required` 는 **배정 단계에서 미리
막기 위한 사본**일 뿐이고, 검증이 카드와 guide 를 대조해 사본이 느슨하면 ⛔ 를 낸다. 안전 방향으로만
작동한다.

---

## 5. 남은 것

- 다른 머신 이관 — 한 대씩(`SCENE_LOOP_OPERATIONS.md §3`). 한 대씩 해야 문제가 생겼을 때 원인이 하나다
- `factory/config._load_our_channels:50-63` · `m3_aivideo_benchmark.py:81-83` 의 2채널 하드코딩
  폴백 → `registry.channel_names()` 위임. 18채널 현실과 어긋나 **에코챔버 차단이 좁아진다**
  (`CLAUDE.md §7` 불변 제약 위반)
- 작품 카드는 검증한 작품만 들어 있다. 다른 머신이 이관될 때 그 머신 담당 작품의 카드를 추가한다
