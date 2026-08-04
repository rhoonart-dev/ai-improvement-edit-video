#!/usr/bin/env python3
"""회차 진행형 쇼츠 생성 루프 — 매일 스케줄(launchd 04:00)로 1회 실행.

정책(사용자 합의):
  - 채널마다 소스 폴더의 회차를 번호 오름차순으로 소비. **회차당 quota(=3)개** 채우면 다음 회차로.
  - 회차 완료 카운트는 **count_mode='public'**: 유튜브에 **공개(public)** 된 장면만 카운트한다.
    비공개(private/unlisted)로 둔 장면은 회차 완료에 안 셈(단, 중복 회피 대상엔 포함).
  - 1회 실행에서 **채널당 1장면**(코드 고정, 설정 노브 아님) → 세 채널이면 하루 최대 3편.
  - "다른 장면"은 생성물(edit_plan.json)의 소스 구간으로 판정. 직전까지 만든 장면과 겹치면
    (IoU≥th 또는 중심 근접) 중복 → 최대 max_retries(=2)회 재생성. 2회 재생성도 중복이면
    보류(미확정)+경고. 할당량 유지(다음날 재시도).
  - **런어웨이 방지**: public 이 quota 미만이라도 '미공개 대기 장면'이 max_pending_unpublished 개
    쌓이면 그 회차 생성을 멈추고 사람의 공개를 기다린다(리뷰 풀 유지).
  - **반려(rejected)는 대기로 세지 않는다**: 사람이 비공개로 돌린(또는 삭제한) 장면은 영원히
    공개되지 않으므로 대기로 세면 그 회차가 영구 교착된다. 카운트에서 빼 생성 슬롯을 돌려주고,
    구간만 중복 회피 대상으로 남긴다 → 반려한 장면을 다시 만들지 않는다(2026-07-30).
  - 다음 회차 소스 파일이 아직 폴더에 없으면 대기.
  - 회차는 start_episode(기본 1)부터 오름차순. 장기 방영작은 이 값으로 시작점을 올린다.

소스 유형 두 가지 (채널 설정 source_type):
  - 'local'  : source_dir 의 회차 파일을 glob 로 발견 → create_shorts --video (기존 동작)
  - 'youtube': source_url(채널 업로드 목록 **또는** 플레이리스트)을 훑어(캐시 24h) 제목에서
    회차를 뽑고, 회차당 **가장 긴 영상** 1건을 그 회차 소스로 삼아 create_shorts --youtube-url.
    같은 회차에 예고(45~97초)·선공개(167초)·쇼츠성 클립·하이라이트(850~1230초)가 섞여
    올라오므로 min_source_duration_sec 로 짧은 것을 걸러야 한다.
    유튜브 소스는 매 실행이 소스를 새로 받아 edit_plan 의 video_path 가 달라지므로, 중복 판정은
    경로가 아니라 회차 디렉토리(outputs/scene_loop/<채널>/ep<N>/)로 한다.

⚠️ 소스 범위는 **권리사 가이드(laeebly licensed_video.guide)** 가 정한다. 채널 전체가 허용되는
   작품(놀라운 토요일: "tvN joy 또는 놀라운 토요일 유튜브 채널 업로드 클립")이 있는가 하면,
   특정 플레이리스트만 허용되는 작품(도깨비 10주년 여행: "해당 링크 플레이리스트에 있는 영상들만
   사용 가능")도 있다. 설정값은 가이드를 사람이 읽고 채운다 — 코드가 추측하지 않는다.
   같은 이유로 title_episode_regex 는 기본값 없이 **필수**다.

공개 여부 판정: 장면 run_id → (DB clip_metadata.ai_video_run_id ↔ clips.video_external_id)
  → 유튜브 Data API videos.list(공개 API 키) 로 privacyStatus. 장면 분류는 classify_scenes:
    - public   : 영상 중 하나라도 status=='public'
    - rejected : 링크된 영상이 있는데 **하나도 조회되지 않고**, 발행 기록상 예약 대기도 아님
                 = 사람이 비공개로 반려/삭제한 것. 대기에서 뺀다.
    - pending  : 그 밖 — unlisted(검수 대기) · 예약 공개 대기 · 아직 업로드 안 된 렌더

  ⚠️ 예약 공개(publishAt)는 공개 시각 전까지 private 이라 조회가 안 된다. 조회 불가만 보고
     반려로 판정하면 예약 대기분이 통째로 반려가 되므로, results/scene_publish_state.json 의
     시각과 reject_grace_days 유예로 '스스로 풀리는 것'과 '영영 안 풀리는 것'을 가른다.

★ 기존 코드는 수정하지 않는다. 생성은 ai-video create_shorts 를 있는 그대로 subprocess 호출.

env(.env 자동 로드): GEMINI_API_KEY, AI_VIDEO_ROOT/WORKTREE/GEN_PY, PIPELINE_DB_URL, REACT_APP_YOUTUBE_API_KEY
실행:
  python scripts/scene_loop.py                 # 전 채널 1회 진행
  python scripts/scene_loop.py --dry-run       # 생성 없이 각 채널 계획 출력
  python scripts/scene_loop.py --status        # 회차별 공개/대기/렌더 현황
  python scripts/scene_loop.py --channel 재미쇼츠
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from envload import load_env
except ImportError:
    def load_env(*a, **k):
        return {}

import channel_registry as registry
import source_cache

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "scene_loop.json"
STATE_PATH = REPO_ROOT / "results" / "scene_loop_state.json"
YT_INDEX_DIR = REPO_ROOT / "results" / "youtube_index"


# ─────────────────────────── 설정/상태 I/O ───────────────────────────

def load_config(path=CONFIG_PATH):
    if not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def resolve_run_config(cfg, log, *, machine=None):
    """실행할 (정책, 채널목록, 모드) 결정. 배정 정본이 있으면 그쪽, 없으면 예전 파일.

    모드는 파일명이 아니라 **'이 머신이 배정 정본에 있는가'** 로 갈린다. 그래서 아직 옮기지 않은
    머신은 이 코드를 pull 해도 예전 config/scene_loop.json 으로 그대로 돈다(무중단 이관).

    🛑 --machine 을 명시했는데 배정에 없으면 예전 파일로 떨어지지 않고 즉시 종료한다. 명시한 의도가
    있는데 조용히 다른 채널 집합으로 도는 것이 가장 위험하다."""
    explicit = machine or os.environ.get("SCENE_LOOP_MACHINE")
    legacy = list(cfg.get("channels") or [])
    try:
        mid = registry.detect_machine_id(explicit=machine,
                                         env=os.environ.get("SCENE_LOOP_MACHINE"),
                                         local=registry.load_machine_local().get("machine"))
        chans = registry.effective_channel_configs(mid)
    except (LookupError, ValueError) as e:
        if explicit:
            sys.exit(f"⛔ 배정 해석 실패: {e}")
        if legacy:
            log(f"⚠ 배정 정본에서 이 머신을 찾지 못해 예전 config/scene_loop.json 을 씁니다 ({e})")
            return cfg, legacy, "legacy"
        sys.exit(f"⛔ 배정 정본에도 예전 설정에도 채널이 없습니다: {e}\n"
                 f"   config/assignments.json 에 이 머신 항목을 추가하거나 "
                 f".env 에 SCENE_LOOP_MACHINE 을 지정하세요")

    policy = dict(registry.load_loop_policy())
    policy.update(registry.load_machine_local().get("overrides") or {})
    log(f"배정 정본 사용: {mid} · 채널 {len(chans)}개")
    if legacy:
        old, new = {c.get("channel") for c in legacy}, {c["channel"] for c in chans}
        if old != new:
            log(f"⚠ 예전 설정과 채널 집합이 다릅니다 — 정본 {sorted(new)} vs 예전 {sorted(old)}. "
                f"검증 후 config/scene_loop.json 을 치우세요")
    return policy, chans, "resolver"


def assert_source_scope(ch):
    """생성 직전 권리 범위 확인 — 선언(type)과 실제 URL 이 어긋나면 소스를 받기 전에 멈춘다.

    🛑 '해당 플레이리스트 영상만 사용 가능' 인 작품에 채널 URL 이 들어가면 채널 전체가 소스가 된다.
    검증 스크립트가 앞에서 막지만, 배정 정본을 거치지 않은 경로(--config 레거시)도 있으므로
    yt-dlp 호출 전 마지막으로 한 번 더 본다."""
    kind, url = ch.get("_source_kind"), ch.get("source_url") or ""
    if kind == "youtube_playlist" and "list=" not in url:
        raise ValueError(f"{ch.get('work_title')}: 플레이리스트 한정 작품인데 소스 URL 이 "
                         f"플레이리스트가 아닙니다({url}) — 권리 범위를 벗어납니다")
    if kind == "youtube_channel" and "list=" in url:
        raise ValueError(f"{ch.get('work_title')}: 채널 소스로 선언됐는데 URL 이 플레이리스트입니다({url})")


def load_state(path=STATE_PATH):
    if Path(path).exists():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return {"channels": {}}


def save_state(state, path=STATE_PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ─────────────────────────── 순수 로직 ───────────────────────────

def discover_episodes(source_dir, video_glob, episode_regex, start_episode=1):
    """소스 폴더의 회차 파일 → [(episode_num, abspath)] 오름차순. 회차번호 못 뽑으면 제외.

    start_episode 미만 회차는 버린다 — 기본 1(=1화부터). 장기 방영작처럼 앞 회차를 쓰지 않는
    작품은 채널 설정의 start_episode 로 시작점을 올린다(예: 놀라운 토요일 410)."""
    pat = re.compile(episode_regex)
    found = []
    for p in glob.glob(str(Path(source_dir) / video_glob)):
        m = pat.search(Path(p).name)
        if m:
            n = int(m.group(1))
            if n >= start_episode:
                found.append((n, str(Path(p).resolve())))
    found.sort(key=lambda x: x[0])
    return found


def slot_key(ch):
    """이 설정 항목의 **진행 슬롯 키** — 상태 파일과 산출물 디렉토리를 가르는 이름.

    한 채널이 작품을 둘 이상 맡을 때 작품별로 진행을 분리한다(channel_registry 가 'slot' 을 채운다).
    작품이 하나면 채널명 그대로라 기존 상태·경로가 그대로 유지된다.
    🛑 슬롯은 진행 관리용 이름일 뿐 **업로드 대상 채널이 아니다** — 발행·공개조회에는 ch['channel'].
    """
    return ch.get("slot") or ch["channel"]


def episode_dir_name(ep_num):
    """회차 → 산출물 디렉토리명. outdir 생성과 회차 스캔이 같은 규칙을 쓰도록 한 곳에 둔다."""
    return f"ep{ep_num:02d}"


def is_url(s):
    return str(s or "").startswith(("http://", "https://"))


def source_label(video_path):
    """로그용 짧은 소스 표기 — 로컬은 파일명, 유튜브는 URL 그대로."""
    return str(video_path) if is_url(video_path) else Path(video_path).name


def source_url_of(ch):
    """유튜브 소스 URL — 채널 업로드 목록이든 플레이리스트든 같은 필드(source_url)로 받는다.
    (channel_url 은 옛 키. 이름이 '채널'로 굳으면 플레이리스트 한정 작품을 표현할 수 없다.)"""
    return ch.get("source_url") or ch.get("channel_url")


def channel_source_type(ch):
    """채널 설정 → 'local' | 'youtube'. source_type 명시 우선, 없으면 source_url 유무로 추론."""
    st = (ch.get("source_type") or "").strip().lower()
    if st:
        return st
    return "youtube" if source_url_of(ch) else "local"


# ── 유튜브 소스: 채널/플레이리스트를 회차 단위로 소비 ──
#   소스가 로컬 폴더가 아니라 유튜브인 작품용. 권리사 가이드에 따라 **채널 전체 허용**(예:
#   놀라운 토요일 — tvN joy/공식채널 클립)일 수도, **특정 플레이리스트 한정**(예: 도깨비
#   10주년 여행 — "해당 링크 플레이리스트에 있는 영상들만 사용 가능")일 수도 있다. yt-dlp 가
#   둘 다 flat 조회로 동일하게 읽으므로 source_url 하나로 처리한다.
#   회차는 **제목**에서 뽑는데 표기 규칙이 작품마다 달라 title_episode_regex 를 필수로 받는다
#   (기본값을 두면 다른 작품에 엉뚱한 규칙이 조용히 적용된다).
#   회차당 여러 클립이 올라오므로 '가장 긴 영상'을 그 회차 소스로 삼는다 — 예고·선공개·쇼츠성
#   클립이 섞이기 때문이며, min_source_duration_sec 로 하한을 둔다.

def parse_index_lines(text):
    """yt-dlp --print 출력 → [{'id','title','duration'}]. 순수.
    구분자는 실제 탭. (셸에서 '\\t' 를 넘기면 리터럴로 들어오므로 그 형태도 받아준다.)"""
    out = []
    for line in (text or "").splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 3:
            parts = line.split("\\t", 2)
        if len(parts) < 3:
            continue
        dur, vid, title = parts[0].strip(), parts[1].strip(), parts[2]
        try:
            d = float(dur)
        except ValueError:
            d = None
        if vid:
            out.append({"id": vid, "title": title, "duration": d})
    return out


def index_episodes(entries, title_regex, start_episode=1, min_duration_sec=0):
    """[{'id','title','duration'}] → {회차: [entry…]}. 순수. title_regex 는 **필수**.
    제목에서 회차를 못 뽑거나 · start_episode 미만이거나 · 너무 짧으면(예고/티저) 제외.

    길이(duration)는 min_duration_sec>0 일 때만 본다 — 길이 미상(None)인 목록에서도
    회차 자체는 살아남아야 하므로, 하한이 없으면 길이로 거르지 않는다.

    제목은 title 뿐 아니라 **alt_titles(과거에 본 제목)도 시도**한다 — 유튜브가 같은 영상을
    다른 언어 제목으로 돌려주면 회차 표기가 사라져 회차가 통째로 누락되기 때문이다(merge_index 참조)."""
    pat = re.compile(title_regex)
    out = {}
    for e in entries:
        m = None
        for t in [e.get("title")] + list(e.get("alt_titles") or []):
            m = pat.search(t or "")
            if m:
                break
        if not m:
            continue
        n = int(m.group(1))
        if n < start_episode:
            continue
        if min_duration_sec > 0:
            d = e.get("duration")
            if d is None or float(d) < min_duration_sec:
                continue          # 하한이 있는데 확인 불가/미달 → 예고편 위험이라 제외
        out.setdefault(n, []).append(e)
    return out


def exclude_entries(entries, exclude_regex):
    """제목이 exclude_regex 에 걸리는 항목을 뺀다. 순수. 미지정이면 그대로 통과.

    권리 범위가 '이 채널 롱폼 중 특정 코너 제외' 로 오는 작품용(B급 스튜디오: **청문회 제외**).

    ⚠️ **alt_titles 도 검사한다** — 한 번이라도 제외 대상 제목으로 보인 영상은 뺀다. index_episodes
    가 alt_titles 로 회차를 되살리므로, 제외를 title 만 보면 '지금은 영어 제목이라 안 걸리는데
    회차는 옛 한글 제목으로 인식되는' 영상이 권리 범위 밖인 채로 소스가 된다.
    ⚠️ 번호 부여 **전**에 걸러야 한다 — 제외분이 서수(ordinal_episodes)에 끼면 회차 번호가 밀린다."""
    if not (exclude_regex or "").strip():
        return list(entries)
    pat = re.compile(exclude_regex)
    out = []
    for e in entries:
        titles = [e.get("title")] + list(e.get("alt_titles") or [])
        if any(pat.search(t or "") for t in titles):
            continue
        out.append(e)
    return out


def ordinal_episodes(entries, start_episode=1, min_duration_sec=0):
    """제목에 회차 표기가 **없는** 채널용 — 업로드 오래된 순서를 서수 회차로 삼는다. 순수.

    flat 목록은 최신순이라 뒤집는다. 회차 표기가 아예 없는 자사 롱폼 채널(커리어데이·B급 스튜디오)은
    정규식으로 뽑을 번호 자체가 없어서 이 방식이 유일하다.

    ⚠️ 서수는 목록 순서에 의존한다 — 채널에서 옛 영상이 삭제되면 번호가 밀린다(새 업로드는 뒤에
    붙으므로 영향 없음). 길이 하한은 번호 부여 **전**에 적용해 번호를 안정시킨다."""
    seq = []
    for e in reversed(entries):
        if min_duration_sec > 0:
            d = e.get("duration")
            if d is None or float(d) < min_duration_sec:
                continue
        seq.append(e)
    return {n: [e] for n, e in enumerate(seq, 1) if n >= start_episode}


def pick_episode_entry(cands):
    """한 회차의 후보 중 실제로 쓸 1건 — 가장 긴 영상(예고·티저 회피). 동률이면 id 사전순. 순수."""
    if not cands:
        return None
    return max(cands, key=lambda e: (float(e.get("duration") or 0), e.get("id") or ""))


def youtube_watch_url(video_id):
    return f"https://www.youtube.com/watch?v={video_id}"


def _yt_index_cache_path(source_url):
    return YT_INDEX_DIR / f"{hashlib.sha1(source_url.encode('utf-8')).hexdigest()[:12]}.json"


def fetch_youtube_index(gen_py, source_url, timeout=1800, lang="ko"):
    """채널/플레이리스트 flat 목록 → [{'id','title','duration'}]. 느리므로 호출자가 캐시한다.
    ⚠️ yt-dlp 콘솔스크립트는 shebang 이 옛 경로일 수 있어 반드시 '-m yt_dlp' 로 부른다.

    ★ `lang` 을 반드시 지정한다(2026-07-28). 유튜브는 영상 하나에 여러 언어의 제목을 갖고 있고,
    지정하지 않으면 어느 쪽을 줄지 유튜브가 정한다. 영어 제목은 뒤가 잘려 회차 표기(`EP.N`)가
    사라지므로 title_episode_regex 가 매칭에 실패하고, **그 회차가 통째로 없는 것처럼 보인다.**
    실측: 도깨비 플레이리스트 45건 중 20건이 영어 제목으로 와 EP3(4건 전부)가 사라졌다.
    에러가 아니라 '소스 없음' 으로 보여서 알아채기 어려운 게 이 버그의 최악점이었다."""
    cmd = [gen_py, "-m", "yt_dlp", "--flat-playlist", "--no-warnings"]
    if lang:
        cmd += ["--extractor-args", f"youtube:lang={lang}"]
    cmd += ["--print", "%(duration)s\t%(id)s\t%(title)s", source_url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"yt-dlp 채널 목록 실패 rc={r.returncode}: {(r.stderr or '')[-300:]}")
    return parse_index_lines(r.stdout)


def merge_index(old_entries, new_entries):
    """이전 캐시 + 새로 받은 목록 → (병합된 entries, 사라진 id 수). 순수.

    lang 을 고정해도 유튜브가 제목을 바꿔 보내는 일이 남을 수 있어, **한 번이라도 본 제목을
    alt_titles 에 쌓아 둔다.** index_episodes 가 title·alt_titles 를 모두 시도하므로 한 번
    회차가 인식된 영상은 이후 제목이 바뀌어도 계속 인식된다.

    ⚠️ **새 목록에 없는 id 는 버린다.** 권리사 가이드가 '해당 플레이리스트에 있는 영상만 사용
    가능' 인 작품이 있어(도깨비), 목록에서 빠진 영상을 캐시에 남겨 두면 권리 범위를 벗어난
    영상을 소스로 쓸 수 있다. 대신 몇 건이 사라졌는지 호출자가 로그로 남긴다(조용한 축소 방지)."""
    prev = {e.get("id"): e for e in (old_entries or []) if e.get("id")}
    merged = []
    for e in new_entries:
        vid = e.get("id")
        if not vid:
            continue
        seen = []
        old = prev.get(vid)
        if old:
            for t in [old.get("title")] + list(old.get("alt_titles") or []):
                if t and t != e.get("title") and t not in seen:
                    seen.append(t)
        out = dict(e)
        if seen:
            out["alt_titles"] = seen[:4]      # 무한정 쌓이지 않게 상한
        merged.append(out)
    dropped = len(set(prev) - {e["id"] for e in merged})
    return merged, dropped


def get_youtube_index(gen_py, source_url, cache_hours, log, now=None):
    """캐시된 채널 인덱스(없거나 오래되면 갱신). 캐시는 results/youtube_index/<해시>.json."""
    p = _yt_index_cache_path(source_url)
    now = now or datetime.now()
    if p.exists():
        try:
            c = json.loads(p.read_text(encoding="utf-8"))
            age_h = (now - datetime.fromisoformat(c["fetched_at"])).total_seconds() / 3600
            if age_h < cache_hours and c.get("entries"):
                log(f"  [youtube] 인덱스 캐시 사용 ({len(c['entries'])}건, {age_h:.1f}시간 전)")
                return c["entries"]
        except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError):
            pass
    log(f"  [youtube] 소스 인덱스 갱신 중 (채널 전체면 수천 건이라 몇 분 걸릴 수 있음) — {source_url}")
    fresh = fetch_youtube_index(gen_py, source_url)
    old = []
    if p.exists():                       # 만료된 캐시라도 제목 누적용으로 읽는다
        try:
            old = (json.loads(p.read_text(encoding="utf-8")) or {}).get("entries") or []
        except (OSError, json.JSONDecodeError, TypeError):
            old = []
    entries, dropped = merge_index(old, fresh)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"source_url": source_url,
                             "fetched_at": now.isoformat(timespec="seconds"),
                             "entries": entries}, ensure_ascii=False), encoding="utf-8")
    kept = sum(1 for e in entries if e.get("alt_titles"))
    log(f"  [youtube] 인덱스 {len(entries)}건 저장"
        + (f" (이전 제목 보존 {kept}건)" if kept else "")
        + (f" ⚠ 목록에서 사라진 {dropped}건은 버렸다(권리 범위 밖 사용 방지)" if dropped else ""))
    return entries


def discover_episodes_youtube(gen_py, ch, cache_hours, log):
    """유튜브 채널/플레이리스트 → [(회차, watch_url)] 오름차순. 회차당 가장 긴 영상 1건."""
    url = source_url_of(ch)
    if not url:
        raise ValueError(f"{ch.get('work_title')!r}: source_url 이 없습니다 "
                         f"(유튜브 소스는 채널 업로드 목록 또는 플레이리스트 URL 필수)")
    order = (ch.get("episode_order") or "").strip().lower()
    regex = ch.get("title_episode_regex")
    if not regex and order != "oldest_first":
        raise ValueError(f"{ch.get('work_title')!r}: title_episode_regex 가 없습니다 — 제목의 회차 "
                         f"표기는 작품마다 달라 기본값을 두지 않습니다(권리사 가이드 확인 후 명시)."
                         f" 회차 표기가 아예 없는 채널은 episode_order='oldest_first' 를 쓴다")
    entries = get_youtube_index(gen_py, url, cache_hours, log)
    before = len(entries)
    entries = exclude_entries(entries, ch.get("title_exclude_regex"))
    if len(entries) != before:
        log(f"  [youtube] 제외 규칙으로 {before - len(entries)}건 제외 → {len(entries)}건 "
            f"(title_exclude_regex={ch.get('title_exclude_regex')!r})")
    if order == "oldest_first":
        idx = ordinal_episodes(entries, ch.get("start_episode", 1),
                               ch.get("min_source_duration_sec", 0))
    else:
        idx = index_episodes(entries, regex,
                             ch.get("start_episode", 1), ch.get("min_source_duration_sec", 0))
    out = []
    for n in sorted(idx):
        e = pick_episode_entry(idx[n])
        if e:
            out.append((n, youtube_watch_url(e["id"])))
    return out


def discover_episodes_for(ch, gen_py=None, cache_hours=24, log=lambda m: None):
    """채널 설정 → [(회차, 소스)] 오름차순. 소스는 로컬 경로 또는 유튜브 URL."""
    if channel_source_type(ch) == "youtube":
        return discover_episodes_youtube(gen_py, ch, cache_hours, log)
    return discover_episodes(ch["source_dir"], ch["video_glob"], ch["episode_regex"],
                             ch.get("start_episode", 1))


def scene_span(edit_plan):
    """edit_plan.json → 이 장면이 커버하는 소스 구간 (min clip_start, max clip_end). 없으면 None."""
    tl = edit_plan.get("timeline") or []
    starts = [c.get("clip_start_sec") for c in tl if c.get("clip_start_sec") is not None]
    ends = [c.get("clip_end_sec") for c in tl if c.get("clip_end_sec") is not None]
    if not starts or not ends:
        return None
    return [float(min(starts)), float(max(ends))]


def _iou(a, b):
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    return (inter / union) if union > 0 else 0.0


def is_duplicate(span, prior_spans, iou_th, center_tol):
    """span 이 기존 장면 중 하나와 같은 장면인가 — 구간 IoU≥th 또는 중심 간격≤center_tol."""
    c = (span[0] + span[1]) / 2
    for p in prior_spans:
        if _iou(span, p) >= iou_th or abs(c - (p[0] + p[1]) / 2) <= center_tol:
            return True
    return False


def merge_scenes(raw, iou_th, center_tol):
    """[{'span','run_id'}] → 같은 장면끼리 접어 [{'span','run_ids':[...]}]. (A/B treat/ctrl·재렌더 합침)"""
    out = []
    for sc in raw:
        sp = sc["span"]
        hit = None
        for o in out:
            if _iou(sp, o["span"]) >= iou_th or \
               abs((sp[0] + sp[1]) / 2 - (o["span"][0] + o["span"][1]) / 2) <= center_tol:
                hit = o
                break
        if hit:
            if sc.get("run_id"):
                hit["run_ids"].append(sc["run_id"])
        else:
            out.append({"span": sp, "run_ids": [sc["run_id"]] if sc.get("run_id") else []})
    return out


# ─────────────────────────── 산출물 스캔 ───────────────────────────

def _run_id_of(job_dir):
    rl = Path(job_dir) / "run_log.json"
    if rl.exists():
        try:
            jid = json.loads(rl.read_text(encoding="utf-8")).get("job_id")
            if jid:
                return jid
        except (OSError, json.JSONDecodeError):
            pass
    return Path(job_dir).name


def existing_output_scenes(scan_roots, video_path):
    """outputs* 에서 input.video_path==video_path 인 edit_plan → [{'span','run_id'}].

    ⚠️ 중복 판정에는 더 이상 쓰지 않는다(2026-07-28) — 채널 구분이 없어 한 작품을 여러 채널이 쓸 때
    다른 채널 산출물을 자기 것으로 오인했다. `rendered_scenes` 참조. 소스 경로로 산출물을 되짚을 때만 쓴다."""
    target = str(Path(video_path).resolve())
    scenes = []
    for root in scan_roots:
        for ep in glob.glob(str(Path(root) / "**" / "edit_plan.json"), recursive=True):
            try:
                d = json.loads(Path(ep).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            vp = ((d.get("input") or {}).get("video_path")) or ""
            try:
                same = bool(vp) and str(Path(vp).resolve()) == target
            except OSError:
                same = False
            if not same:
                continue
            sp = scene_span(d)
            if sp:
                scenes.append({"span": sp, "run_id": _run_id_of(Path(ep).parent)})
    return scenes


def episode_output_scenes(scan_roots, channel, ep_num):
    """scene_loop 가 만든 (채널,회차) 산출물 → [{'span','run_id'}].

    유튜브 소스는 매 실행이 소스를 새 outdir 로 내려받아 edit_plan.input.video_path 가 매번
    달라진다 → 경로 매칭(existing_output_scenes)이 안 먹으므로 회차 디렉토리로 찾는다."""
    scenes = []
    for root in scan_roots:
        pat = str(Path(root) / "scene_loop" / channel / episode_dir_name(ep_num) / "**" / "edit_plan.json")
        for ep in glob.glob(pat, recursive=True):
            try:
                d = json.loads(Path(ep).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            sp = scene_span(d)
            if sp:
                scenes.append({"span": sp, "run_id": _run_id_of(Path(ep).parent)})
    return scenes


def rendered_scenes(state, channel, ep_num, video_path, scan_roots, iou_th, center_tol):
    """이 (채널,회차)의 '서로 다른 렌더 장면' 목록 [{'span','run_ids'}] — 상태 + 기존 산출물 병합.

    ★ 중복 판정은 **채널 단위로 닫혀 있다.** 한 작품을 여러 채널이 쓸 수 있고(같은 머신이어도),
    그때 채널끼리는 서로 어떤 장면을 만들었는지 볼 필요가 없다 — 각자 자기 채널 안에서만 안 겹치면 된다.
    상태는 원래 channel 로 키가 잡혀 있었지만, 로컬 소스는 `existing_output_scenes` 가 outputs 전체를
    video_path 로만 훑어 **다른 채널 산출물까지 자기 것으로 오인**했다(2026-07-28 수정). 그러면 채널B가
    채널A 장면을 피해 다니고, pending 만 부풀어 max_pending_unpublished 에 조기 도달해 멈춘다.
    이제 소스 종류와 무관하게 `outputs*/scene_loop/<채널>/ep<NN>/` 만 스캔한다.

    ※ 이 경로 밖의 산출물(수동 생성분·과거 outputs_ab)은 스캔되지 않는다 —
      SCENE_LOOP_OPERATIONS.md §6-2 대로 상태 파일에 심어서 반영한다."""
    st = (((state.get("channels") or {}).get(channel) or {}).get("episodes") or {}) \
        .get(str(ep_num), {}).get("scenes", [])
    raw = [{"span": s["span"], "run_id": s.get("run_id")} for s in st if s.get("span")]
    raw += episode_output_scenes(scan_roots, channel, ep_num)
    return merge_scenes(raw, iou_th, center_tol)


def record_scene(state, slot, work_title, ep_num, video_path, span, run_id, job_dir,
                 channel=None):
    # 🛑 video_path 는 반드시 str 로 넣는다 — source_cache.ensure_episode_source 가 Path 를 돌려주므로
    # 그대로 담으면 save_state 의 json.dumps 가 TypeError(PosixPath not JSON serializable) 로 죽는다.
    # 생성이 끝난 **뒤**에 터지는 자리라, 30~90분 쓴 렌더가 상태에 기록되지 않는다(2026-07-29 실측:
    # 로컬 소스 채널 3곳이 전부 이걸로 실패). 렌더 스캔이 산출물을 주워 유실은 없었지만 매일 반복된다.
    # 🛑 상태 키는 슬롯이지만 **업로드 대상 채널명('channel')을 반드시 함께 적는다** — 다작품 채널은
    # 슬롯이 '재미쇼츠·유미의 세포들 시즌3' 처럼 채널명과 달라, 이 필드가 없으면
    # scene_publish_loop 가 슬롯명을 채널명으로 넘겨 발행이 미등록 채널로 하드 실패한다(2026-08-04 실측).
    ch = state.setdefault("channels", {}).setdefault(slot, {"work_title": work_title, "episodes": {}})
    if channel:
        ch["channel"] = channel
    ep = ch.setdefault("episodes", {}).setdefault(str(ep_num), {"video_path": str(video_path), "scenes": []})
    ep["scenes"].append({"span": span, "run_id": run_id, "job_dir": job_dir,
                         "accepted_at": datetime.now().isoformat(timespec="seconds")})


# ─────────────────────────── 공개 여부 (DB + 유튜브 API키) ───────────────────────────

def db_run_videos(conn, channel, run_ids):
    """{run_id: [video_external_id,...]} — 이 채널에서 발행돼 링크된 영상ID. conn None/에러면 {}."""
    if conn is None or not run_ids:
        return {}
    out = {}
    with conn.cursor() as c:
        c.execute("""
            select m.ai_video_run_id, c.video_external_id
            from clips c
            join clip_metadata m on m.clip_id = c.id
            join channels ch on ch.id = c.channel_id
            where ch.name = %s and m.ai_video_run_id = any(%s)
              and c.video_external_id is not null
        """, (channel, list(run_ids)))
        for run_id, vid in c.fetchall():
            out.setdefault(run_id, []).append(vid)
    return out


def youtube_statuses(video_ids, api_key):
    """{video_id: privacyStatus} — 공개 API 키로 조회. 실패 시 예외.

    🛑 **응답에 없는 id 는 dict 에 안 들어온다.** 공개 API 키는 private 영상을 아예 돌려주지
    않으므로, '링크는 있는데 조회 결과에 없음' = 비공개(사람이 반려) 또는 삭제됨이다.
    이 구분이 필요해서 public 집합만 돌려주던 걸 상태 dict 로 바꿨다(2026-07-30).
    """
    out = {}
    ids = [v for v in dict.fromkeys(video_ids) if v]
    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        q = urllib.parse.urlencode({"part": "status", "id": ",".join(batch), "key": api_key})
        with urllib.request.urlopen("https://www.googleapis.com/youtube/v3/videos?" + q, timeout=20) as r:
            data = json.loads(r.read())
        for it in data.get("items", []):
            out[it["id"]] = (it.get("status") or {}).get("privacyStatus")
    return out


def youtube_public_ids(video_ids, api_key):
    """video_ids 중 privacyStatus=='public' 인 것들의 집합."""
    return {v for v, s in youtube_statuses(video_ids, api_key).items() if s == "public"}


# 장면 분류 — 회차 카운트와 브레이크 판정의 기준
SCENE_PUBLIC = "public"        # 공개됨 → 회차 quota 에 카운트
SCENE_PENDING = "pending"      # 검수 대기·예약 대기·미업로드 → 기다리면 풀린다
SCENE_REJECTED = "rejected"    # 비공개/삭제 → 사람이 공개할 수 없다. 브레이크에서 제외한다

# 발행 상태 파일(scene_publish_loop 가 쓴다) — 경로 정본을 여기 둔다
PUB_STATE_PATH = REPO_ROOT / "results" / "scene_publish_state.json"

# 올린 뒤 이 기간 안에 조회가 안 되면 '아직 공개 전'으로 본다(예약 공개는 시각 전까지 private).
# 지나도 안 보이면 사람이 비공개로 돌린 것으로 판정한다. loop_policy 의 reject_grace_days 로 덮는다.
REJECT_GRACE_DAYS = 7


def load_publish_records(path=None):
    """run_id → 발행 기록(scene_publish_state.json 의 scenes). 없으면 {}."""
    p = Path(path or PUB_STATE_PATH)
    try:
        return (json.loads(p.read_text(encoding="utf-8")) or {}).get("scenes") or {}
    except (OSError, json.JSONDecodeError, AttributeError):
        return {}


def _parse_dt(s):
    """ISO 문자열 → tz-aware datetime. naive 면 로컬 tz 로 본다. 실패하면 None."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s))
    except ValueError:
        return None
    return d if d.tzinfo else d.astimezone()


def _awaiting_publish(rec, now, grace_days):
    """이 발행 기록이 '아직 공개 전(대기)'로 볼 만한가.

    예약 공개(publishAt)는 **공개 시각 전까지 private** 이라 공개 API 키 조회에 안 나온다.
    그래서 조회 불가만 보고 반려로 판정하면 예약 대기분이 통째로 반려가 된다 — 그런데
    scene_publish_loop 의 **기본 동작이 예약 공개**라 브레이크가 통째로 무력해진다
    (2026-07-30 실측: 숏테토칩 EP1 이 예약 1건 때문에 상한이 잘못 풀렸다).

    예약 시각을 알면 그걸 쓰고, 모르면 업로드 시각 + 유예로 본다 — 사람이 Studio 에서 직접
    예약을 걸면 우리 상태 파일엔 시각이 안 남기 때문이다. 유예가 지나도 안 보이면 반려다:
    예약분은 시각이 되면 스스로 public 이 되지만 반려분은 영원히 안 보이므로, 유예가
    '스스로 풀리는 것'과 '영영 안 풀리는 것'을 가른다.
    """
    ref = _parse_dt(rec.get("scheduled_publish_at"))
    if ref is not None:
        return now < ref + timedelta(days=grace_days)
    up = _parse_dt(rec.get("published_at"))
    return up is not None and now < up + timedelta(days=grace_days)


def classify_scenes(scenes, conn, channel, api_key, publish_records=None,
                    now=None, grace_days=REJECT_GRACE_DAYS):
    """scenes(merge_scenes 결과)를 public|pending|rejected 로 분류. scenes 와 같은 순서의 리스트.

    🛑 **rejected 를 '공개 대기'로 세면 안 된다.** pending 은 "기다리면 풀리는 것"이라는 뜻이고,
    그래서 max_pending_unpublished 에 도달하면 루프가 멈춰 사람을 기다린다. 그런데 사람이
    **비공개로 반려한** 장면은 영원히 공개되지 않으므로, 그걸 대기로 세면 그 회차는 **영구 교착**이
    된다(2026-07-30 실측: 너굴안방 EP5·흥행수집 EP4 가 반려 1개씩을 대기로 점유 중이었고, 반려가
    3개 쌓이면 그 회차는 죽는다). 반려는 카운트에서 빼서 생성 슬롯을 되돌려 준다.

    🛑 **거꾸로, 예약 대기분을 반려로 세도 안 된다** — _awaiting_publish 참고. 공개 API 키는
    private 을 안 돌려주므로 '예약 대기'와 '반려'가 조회상 똑같이 보인다. 발행 기록의 시각으로
    가른다.

    ⚠️ 반려 장면의 **구간은 중복 회피 대상에 그대로 남긴다**(호출부가 info['scenes'] 를 통째로
    prior_spans 에 쓴다) — 안 그러면 사람이 버린 그 구간을 루프가 다시 만든다.
    """
    recs = load_publish_records() if publish_records is None else publish_records
    now = now or datetime.now().astimezone()
    run_ids = sorted({r for sc in scenes for r in sc["run_ids"]})
    run2vid = db_run_videos(conn, channel, run_ids)
    all_vids = [v for vs in run2vid.values() for v in vs]
    st = youtube_statuses(all_vids, api_key) if all_vids else {}
    kinds = []
    for sc in scenes:
        vids = [v for r in sc["run_ids"] for v in run2vid.get(r, [])]
        if any(st.get(v) == SCENE_PUBLIC for v in vids):
            kinds.append(SCENE_PUBLIC)
        elif vids and not any(v in st for v in vids):
            # 링크는 있는데 전부 조회 불가 = 비공개/삭제 **또는** 예약 공개 대기
            awaiting = any(_awaiting_publish(recs.get(r) or {}, now, grace_days)
                           for r in sc["run_ids"])
            kinds.append(SCENE_PENDING if awaiting else SCENE_REJECTED)
        else:
            kinds.append(SCENE_PENDING)    # unlisted 이거나 아직 업로드 안 된 렌더
    return kinds


def count_public_scenes(scenes, conn, channel, api_key, publish_records=None):
    """scenes 중 '공개된 장면' 수. 장면의 영상 중 하나라도 public 이면 공개."""
    return classify_scenes(scenes, conn, channel, api_key,
                           publish_records=publish_records).count(SCENE_PUBLIC)


# ─────────────────────────── 생성 (ai-video 그대로 호출) ───────────────────────────

def build_cmd(gen_py, work_title, video_path, outdir, gen_flags, ep_num=None, subtitle=None):
    """소스가 URL 이면 --youtube-url(ai-video 가 직접 받아 씀), 아니면 --video. 순수.

    작품 리서치는 **켠 상태**로 돌린다 — 인물명·관계를 모르면 장면 분석이 등장인물을 틀린다
    (2026-07-26 실측: 너굴안방 재생성 사유). ai-video 는 `--episode N` 을 주면 리서치를 1~N회로
    한정해 이후 회차 스포일러를 막으므로, 루프가 아는 회차 번호를 반드시 같이 넘긴다.

    subtitle 은 **권리사 제공 자막(works.json constraints.subtitles=='provided')일 때만** 넘어온다.
    유튜브에서 함께 받아지는 자막은 자동 생성일 확률이 높아 오자막이 분석·화면에 그대로 들어가므로
    쓰지 않는다 — 그런 작품은 자막 없이 돌리고 --no-subtitles 가 gen_flags 에 붙는다(2026-07-29 합의).
    호출자(process_channel)가 카드 값을 보고 걸러 넘긴다."""
    src = ["--youtube-url", video_path] if is_url(video_path) else ["--video", str(video_path)]
    ep = ["--episode", str(ep_num)] if ep_num is not None else []
    sub = ["--subtitle", str(subtitle)] if subtitle else []
    return [gen_py, "-m", "app.cli", "create_shorts",
            "--title", work_title, *src, *sub, *ep,
            "--max-shorts", "1", "--outdir", outdir, *gen_flags]


def run_generation(cmd, worktree, ai_video_root, timeout):
    env = dict(os.environ, PYTHONPATH=worktree, AI_VIDEO_ROOT=ai_video_root)
    return subprocess.run(cmd, cwd=worktree, env=env, capture_output=True, text=True, timeout=timeout)


def newest_job_dir(outdir):
    cands = glob.glob(str(Path(outdir) / "*" / "edit_plan.json"))
    return str(Path(max(cands, key=os.path.getmtime)).parent) if cands else None


def save_gen_output(outdir, cmd, rc, stdout, stderr):
    """실패한 생성의 stdout/stderr 전문을 시도 디렉토리에 남기고 그 경로를 돌려준다.

    로그에는 stderr 꼬리 300자만 적는다(밤새 도는 로그를 트레이스백으로 채우지 않으려고).
    그런데 원인은 대개 꼬리 밖에 있다 — Gemini 재시도 경고(`[WARN] …`)는 stdout 으로 나가는데
    그건 통째로 버려져, 2026-07-30·31 실패 3건의 원인(응답 잘림)을 찾는 데 로그를 거슬러
    올라가야 했다. 전문은 파일에 두고 로그는 그대로 짧게 유지한다."""
    def text(v):
        # 타임아웃 경로(TimeoutExpired)는 파이썬 판마다 bytes 로 올 수 있다
        if isinstance(v, bytes):
            return v.decode("utf-8", "replace")
        return v or ""

    path = Path(outdir) / "gen_output.log"
    try:
        path.write_text(
            f"$ {' '.join(str(c) for c in cmd)}\nrc={rc}\n"
            f"\n===== stdout =====\n{text(stdout)}\n"
            f"\n===== stderr =====\n{text(stderr)}\n",
            encoding="utf-8")
        return str(path)
    except OSError as e:
        return f"(저장 실패: {e})"


# ─────────────────────────── 채널 상태 판정 ───────────────────────────

def channel_plan(cfg, ch, state, conn, api_key, scan_roots, gen_py=None, log=lambda m: None):
    """이 채널이 지금 무엇을 할지 판정.
    반환 (action, ep_num, vp, info) — action ∈ {'gen','wait_publish','done_all','no_source'}."""
    quota = cfg["quota_per_episode"]
    max_pending = cfg.get("max_pending_unpublished", quota)
    iou_th, ctol = cfg["dup_iou_threshold"], cfg["dup_center_tolerance_sec"]
    pub_recs = load_publish_records()          # 예약 대기 ↔ 반려를 가르는 근거
    grace = cfg.get("reject_grace_days", REJECT_GRACE_DAYS)
    eps = discover_episodes_for(ch, gen_py, cfg.get("youtube_index_cache_hours", 24), log)
    if not eps:
        return ("no_source", None, None, {"eps": []})
    for ep_num, vp in eps:
        # 진행 키는 slot_key(상태·산출물·중복판정), 공개 조회는 실제 채널명 — 섞으면 안 된다.
        scenes = rendered_scenes(state, slot_key(ch), ep_num, vp, scan_roots, iou_th, ctol)
        kinds = classify_scenes(scenes, conn, ch["channel"], api_key,
                                publish_records=pub_recs, grace_days=grace)
        pub = kinds.count(SCENE_PUBLIC)
        # 브레이크는 pending 만 본다 — 반려(rejected)는 사람이 공개해 줄 수 없으므로 제외한다.
        # scenes 는 통째로 넘긴다: 반려 구간도 중복 회피 대상이어야 한다(classify_scenes 참고).
        info = {"eps": eps, "rendered": len(scenes), "public": pub,
                "pending": kinds.count(SCENE_PENDING), "rejected": kinds.count(SCENE_REJECTED),
                "scenes": scenes, "kinds": kinds}
        if pub >= quota:
            continue                                  # 이 회차 공개 완료 → 다음 회차 검사
        if info["pending"] >= max_pending:
            return ("wait_publish", ep_num, vp, info)  # 대기분 가득 → 사람 공개 대기
        return ("gen", ep_num, vp, info)               # 생성 필요
    return ("done_all", None, None, {"eps": eps})


# ─────────────────────────── 채널 1회 처리 ───────────────────────────

def process_channel(cfg, ch, state, conn, api_key, gen_py, worktree, ai_video_root, dry_run, log,
                    sources_root=None):
    sources_root = sources_root or registry.default_sources_root()
    quota = cfg["quota_per_episode"]
    scan_roots = [str(Path(ai_video_root) / d) for d in cfg.get("outputs_scan_dirs", ["outputs"])]
    tag = f"[{ch['channel']} · {ch['work_title']}]"
    if ch.get("_paused"):
        log(f"{tag} ⏸ 착수 전(works.json paused) → 건너뜀")
        return False
    assert_source_scope(ch)          # 권리 범위 — 소스를 받기 전에 본다
    try:
        action, ep_num, vp, info = channel_plan(cfg, ch, state, conn, api_key, scan_roots,
                                                gen_py, log)
    except urllib.error.URLError as e:
        log(f"{tag} ⚠ 유튜브 공개상태 조회 실패({e}) → 오늘 이 채널 스킵(오판 방지)")
        return
    except Exception as e:  # noqa: BLE001 — DB 등 조회 실패
        log(f"{tag} ⚠ 공개 카운트 조회 실패({type(e).__name__}: {e}) → 오늘 이 채널 스킵")
        return

    if action == "no_source":
        where = source_url_of(ch) if channel_source_type(ch) == "youtube" else ch.get("source_dir")
        log(f"{tag} 쓸 수 있는 회차 없음 → 스킵 ({where})")
        return
    if action == "done_all":
        log(f"{tag} 발견된 회차({[e for e,_ in info['eps']]}) 모두 공개 {quota}개 충족 → 다음 회차 소스 대기")
        return
    rej = f", 반려 {info['rejected']}(카운트 제외)" if info.get("rejected") else ""
    if action == "wait_publish":
        log(f"{tag} EP{ep_num}: 공개 {info['public']}/{quota}, 미공개 대기 {info['pending']}개"
            f"(상한 {cfg.get('max_pending_unpublished', quota)}){rej} → 생성 멈춤. 발행/공개 필요")
        return

    # action == 'gen'
    log(f"{tag} EP{ep_num}: 공개 {info['public']}/{quota} "
        f"(렌더 {info['rendered']}, 미공개 {info['pending']}{rej})"
        f" → 이번에 1장면 생성 (소스 {source_label(vp)})")
    if dry_run:
        log(f"{tag}   (dry-run) 생성 생략")
        return

    iou_th, ctol = cfg["dup_iou_threshold"], cfg["dup_center_tolerance_sec"]
    attempts = 1 + cfg["max_retries"]
    prior_spans = [sc["span"] for sc in info["scenes"]]
    # 생성 플래그는 작품별이 우선 — 자막 유무가 작품마다 달라 전역 플래그로는 공존할 수 없다
    gen_flags = ch.get("gen_flags") or cfg.get("gen_flags") or cfg.get("gen_flags_base") or []

    # 소스는 작품 폴더에 한 번만 받아 재사용한다. 회차당 3장면을 채우려면 3번 실행하는데,
    # 예전엔 매 실행이 같은 영상을 새로 받아 100MB 대 파일이 3벌씩 쌓였다(2026-07-28 실측).
    try:
        vp, cached_sub = source_cache.ensure_episode_source(
            ch, ep_num, vp, gen_py=gen_py, ai_video_root=ai_video_root,
            sources_root=sources_root, log=lambda m: log(f"{tag}{m}"))
    except (ValueError, RuntimeError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        log(f"{tag}   ✗ 소스 준비 실패 → 이 채널 오늘 종료: {e}")
        return

    # 🛑 자막은 **권리사 제공분(subtitles='provided')만** 쓴다. 유튜브에서 함께 받아지는 자막은
    # 자동 생성일 확률이 높아 오자막이 그대로 분석·화면에 들어간다 — 제공 자막이 없는 작품은
    # 자막을 아예 주지 않고 --no-subtitles 로 돌린다(2026-07-29 합의). 캐시에 srt 가 있어도
    # 참고용으로만 남기고 넘기지 않는다.
    sub_path = cached_sub if ch.get("_subtitles") == "provided" else None
    if cached_sub and sub_path is None:
        log(f"{tag}   [자막] 캐시에 자막이 있으나 제공 자막이 아니라 쓰지 않습니다"
            f"(카드 subtitles={ch.get('_subtitles')!r})")

    for attempt in range(1, attempts + 1):
        outdir = str(Path(ai_video_root) / "outputs" / "scene_loop" / slot_key(ch) /
                     episode_dir_name(ep_num) / f"try{attempt}_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        Path(outdir).mkdir(parents=True, exist_ok=True)
        cmd = build_cmd(gen_py, ch["work_title"], vp, outdir, gen_flags, ep_num, sub_path)
        log(f"{tag}   시도 {attempt}/{attempts}: {' '.join(cmd[:6])} … → {outdir}")
        try:
            r = run_generation(cmd, worktree, ai_video_root, cfg["gen_timeout_sec"])
        except subprocess.TimeoutExpired as e:
            saved = save_gen_output(outdir, cmd, "timeout", e.stdout, e.stderr)
            log(f"{tag}   ✗ 생성 타임아웃({cfg['gen_timeout_sec']}s) → 이 채널 오늘 종료. 전문: {saved}")
            return
        job_dir = newest_job_dir(outdir)
        if r.returncode != 0 or not job_dir:
            saved = save_gen_output(outdir, cmd, r.returncode, r.stdout, r.stderr)
            log(f"{tag}   ✗ 생성 실패 rc={r.returncode} → 이 채널 오늘 종료. 전문: {saved}\n"
                f"      stderr꼬리: {(r.stderr or r.stdout or '')[-300:]}")
            return
        try:
            plan = json.loads((Path(job_dir) / "edit_plan.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log(f"{tag}   ✗ edit_plan 읽기 실패({e}) → 이 채널 오늘 종료")
            return
        span = scene_span(plan)
        if span is None:
            log(f"{tag}   ✗ 장면 구간 없음(timeline 비어있음) → 이 채널 오늘 종료 (job={job_dir})")
            return
        if is_duplicate(span, prior_spans, iou_th, ctol):
            log(f"{tag}   ↻ 중복 장면 {span} (기존과 겹침) → 재생성")
            continue
        run_id = _run_id_of(job_dir)
        record_scene(state, slot_key(ch), ch["work_title"], ep_num, vp, span, run_id, job_dir,
                     channel=ch["channel"])
        save_state(state)
        log(f"{tag}   ✓ 새 장면 확정(미공개) {span} (run={run_id}) — 공개 {info['public']}/{quota} 유지."
            f" 공개 처리하면 회차 카운트 반영")
        return
    log(f"{tag}   ⚠ {attempts}회 모두 이전과 같은 장면 → 보류(미확정). 다음날 재시도. 수동 확인 권장 (EP{ep_num})")


# ─────────────────────────── status ───────────────────────────

def cmd_status(cfg, state, conn, api_key, ai_video_root, log, gen_py=None):
    scan_roots = [str(Path(ai_video_root) / d) for d in cfg.get("outputs_scan_dirs", ["outputs"])]
    quota = cfg["quota_per_episode"]
    iou_th, ctol = cfg["dup_iou_threshold"], cfg["dup_center_tolerance_sec"]
    pub_recs = load_publish_records()
    grace = cfg.get("reject_grace_days", REJECT_GRACE_DAYS)
    for ch in cfg["channels"]:
        if ch.get("_paused"):
            log(f"[{ch['channel']} · {ch['work_title']}]  ⏸ 착수 전(works.json paused)")
            continue
        eps = discover_episodes_for(ch, gen_py, cfg.get("youtube_index_cache_hours", 24), log)
        log(f"[{ch['channel']} · {ch['work_title']}]  회차: {[e for e,_ in eps] or '없음'}")
        for ep_num, vp in eps:
            scenes = rendered_scenes(state, slot_key(ch), ep_num, vp, scan_roots, iou_th, ctol)
            try:
                kinds = classify_scenes(scenes, conn, ch["channel"], api_key,
                                        publish_records=pub_recs, grace_days=grace)
                pub, pubs = kinds.count(SCENE_PUBLIC), str(kinds.count(SCENE_PUBLIC))
            except Exception as e:  # noqa: BLE001
                kinds, pub, pubs = None, None, f"조회실패({type(e).__name__})"
            mark = "✓완료" if (pub is not None and pub >= quota) else "…진행"
            # 🛑 `if kinds` 로 쓰면 렌더 0인 회차(kinds==[])가 조회실패와 같은 '?' 로 찍힌다
            rej = kinds.count(SCENE_REJECTED) if kinds is not None else 0
            log(f"    EP{ep_num}: 공개 {pubs}/{quota} {mark}  (렌더 {len(scenes)}, "
                f"미공개 {kinds.count(SCENE_PENDING) if kinds is not None else '?'}"
                f"{f', 반려 {rej}' if rej else ''})  "
                f"구간={[[round(x,1) for x in s['span']] for s in scenes]}")


# ─────────────────────────── main ───────────────────────────

def _connect_db(log):
    url = os.environ.get("PIPELINE_DB_URL")
    if not url:
        log("⚠ PIPELINE_DB_URL 미설정 — 공개 카운트 불가")
        return None
    try:
        import psycopg
        return psycopg.connect(url)
    except Exception as e:  # noqa: BLE001
        log(f"⚠ DB 연결 실패({type(e).__name__}: {e}) — 공개 카운트 불가")
        return None


def main():
    load_env()
    ap = argparse.ArgumentParser(description="회차 진행형 쇼츠 생성 루프 (공개분만 카운트)")
    ap.add_argument("--dry-run", action="store_true", help="생성 없이 계획만 출력")
    ap.add_argument("--status", action="store_true", help="회차별 공개/대기/렌더 현황")
    ap.add_argument("--channel", help="특정 채널만 처리(예: 재미쇼츠)")
    ap.add_argument("--machine", help="배정 정본의 머신 id(자동 감지 대신 명시)")
    ap.add_argument("--config", default=str(CONFIG_PATH))
    a = ap.parse_args()

    state = load_state()

    def log(m):
        print(m, flush=True)

    cfg, channels, mode = resolve_run_config(load_config(a.config), log, machine=a.machine)
    cfg = dict(cfg)
    cfg["channels"] = channels

    # 소스 캐시 위치 — scene_loop.local.json 의 sources_root 가 정본, 없으면 <레포>/../sources
    sources_root = registry.load_machine_local().get("sources_root") or registry.default_sources_root()

    ai_video_root = os.environ.get("AI_VIDEO_ROOT") or str(Path.home() / "ves" / "ai-video")
    worktree = os.environ.get("AI_VIDEO_WORKTREE", ai_video_root)
    gen_py = os.environ.get("AI_VIDEO_GEN_PY", str(Path(ai_video_root) / ".venv" / "bin" / "python"))
    api_key = os.environ.get(cfg.get("youtube_api_key_env", "REACT_APP_YOUTUBE_API_KEY"))
    if cfg.get("count_mode") == "public" and not api_key:
        sys.exit(f"count_mode=public 인데 {cfg.get('youtube_api_key_env')} 미설정 — .env 확인")

    conn = _connect_db(log)
    try:
        if a.status:
            cmd_status(cfg, state, conn, api_key, ai_video_root, log, gen_py)
            return
        if "GEMINI_API_KEY" not in os.environ and not a.dry_run:
            sys.exit("GEMINI_API_KEY 미설정 — .env 로드 실패? (생성 불가)")

        log(f"=== scene_loop {datetime.now().isoformat(timespec='seconds')} "
            f"| root={ai_video_root} | mode={cfg.get('count_mode')} | dry_run={a.dry_run} ===")
        if a.channel:
            channels = [c for c in channels if c["channel"] == a.channel]
            if not channels:
                sys.exit(f"채널 '{a.channel}' 매니페스트에 없음")
        for ch in channels:
            try:
                process_channel(cfg, ch, state, conn, api_key, gen_py, worktree,
                                 ai_video_root, a.dry_run, log, sources_root=sources_root)
            except Exception as e:  # noqa: BLE001 — 한 채널 실패가 다른 채널을 막지 않게
                log(f"[{ch['channel']}] 처리 중 예외: {type(e).__name__}: {e}")
        log("=== scene_loop 종료 ===")
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()
