#!/usr/bin/env python3
"""회차 진행형 쇼츠 생성 루프 — 매일 스케줄(launchd 04:00)로 1회 실행.

정책(사용자 합의):
  - 채널마다 소스 폴더의 회차를 번호 오름차순으로 소비. **회차당 quota(=3)개** 채우면 다음 회차로.
  - 회차 완료 카운트는 **count_mode='public'**: 유튜브에 **공개(public)** 된 장면만 카운트한다.
    비공개(private/unlisted)로 둔 장면은 회차 완료에 안 셈(단, 중복 회피 대상엔 포함).
  - 1회 실행에서 채널당 per_run_scenes_per_channel(=1) 장면 생성 → 세 채널이면 하루 최대 3편.
  - "다른 장면"은 생성물(edit_plan.json)의 소스 구간으로 판정. 직전까지 만든 장면과 겹치면
    (IoU≥th 또는 중심 근접) 중복 → 최대 max_retries(=2)회 재생성. 2회 재생성도 중복이면
    보류(미확정)+경고. 할당량 유지(다음날 재시도).
  - **런어웨이 방지**: public 이 quota 미만이라도 '미공개 대기 장면'이 max_pending_unpublished 개
    쌓이면 그 회차 생성을 멈추고 사람의 공개를 기다린다(리뷰 풀 유지).
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

공개 여부 판정(코드 미변경): 장면 run_id → (DB clip_metadata.ai_video_run_id ↔ clips.video_external_id)
  → 유튜브 Data API videos.list(공개 API 키) 로 privacyStatus. API키로 조회되고 status=='public' 인
  영상이 하나라도 있으면 그 장면=공개. (private 는 API키 조회에서 아예 안 나오므로 자동 제외.)

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
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from envload import load_env
except ImportError:
    def load_env(*a, **k):
        return {}

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "scene_loop.json"
STATE_PATH = REPO_ROOT / "results" / "scene_loop_state.json"
YT_INDEX_DIR = REPO_ROOT / "results" / "youtube_index"
# 유튜브 목록 조회 언어 — 자동 번역 제목이 아니라 원어로 고정한다(fetch_youtube_index 주석 참고).
DEFAULT_YT_LANG = "ko"


# ─────────────────────────── 설정/상태 I/O ───────────────────────────

def load_config(path=CONFIG_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


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


def slot_key(ch):
    """이 설정 항목의 **진행 슬롯 키** — 상태(scene_loop_state)와 산출물 디렉토리를 가르는 이름.

    한 채널이 작품을 **순차로** 두 개 이상 소비할 때(예: 재미쇼츠 = 유미의 세포들 시즌3 → 언더커버셰프)
    둘 다 EP1 부터 시작하므로, 채널명만으로 키를 잡으면 앞 작품의 EP1 장면이 뒤 작품의 EP1 진행분으로
    오인된다. slot 을 주면 그 이름으로 분리한다. 미지정이면 채널명(기존 동작 그대로).
    ⚠ 슬롯은 진행 관리용 이름일 뿐 **업로드 대상 채널이 아니다** — 발행에는 항상 ch['channel'] 을 쓴다."""
    return ch.get("slot") or ch["channel"]


def is_queued(ch):
    """순차 대기 항목인가 — True 면 이번 실행에서 건너뛴다.
    같은 채널에 작품을 여러 개 등록해두고 앞 작품이 끝나면 사람이 이 플래그를 내려 다음 작품으로 넘긴다
    ('소스가 더 없음'을 코드가 작품 종료로 단정하면, 권리사가 다음 회차를 아직 안 올린 것뿐인
     경우에도 다음 작품으로 넘어가 버린다 — 그래서 전환은 사람이 명시한다)."""
    return bool(ch.get("queued"))


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
    회차 자체는 살아남아야 하므로, 하한이 없으면 길이로 거르지 않는다."""
    pat = re.compile(title_regex)
    out = {}
    for e in entries:
        m = pat.search(e.get("title") or "")
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

    권리 범위가 '이 채널 롱폼 중 특정 코너 제외'로 오는 작품용(B급 스튜디오: **청문회 제외**).
    ⚠ 번호 부여 **전**에 걸러야 한다 — 제외분이 서수(oldest_first)에 끼면 회차 번호가 밀린다."""
    if not (exclude_regex or "").strip():
        return list(entries)
    pat = re.compile(exclude_regex)
    return [e for e in entries if not pat.search(e.get("title") or "")]


def pick_episode_entry(cands):
    """한 회차의 후보 중 실제로 쓸 1건 — 가장 긴 영상(예고·티저 회피). 동률이면 id 사전순. 순수."""
    if not cands:
        return None
    return max(cands, key=lambda e: (float(e.get("duration") or 0), e.get("id") or ""))


def youtube_watch_url(video_id):
    return f"https://www.youtube.com/watch?v={video_id}"


def _yt_index_cache_path(source_url, lang=None):
    key = f"{source_url}|lang={lang or ''}"
    return YT_INDEX_DIR / f"{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}.json"


def fetch_youtube_index(gen_py, source_url, timeout=1800, lang=None):
    """채널/플레이리스트 flat 목록 → [{'id','title','duration'}]. 느리므로 호출자가 캐시한다.
    ⚠️ yt-dlp 콘솔스크립트는 shebang 이 옛 경로일 수 있어 반드시 '-m yt_dlp' 로 부른다.

    ⚠️ lang 을 **반드시** 준다(기본 'ko'). 유튜브는 자동 번역 제목을 조회 로케일에 따라 돌려주는데,
    같은 채널이 어제는 'B급 청문회', 오늘은 'B-Class Hearing' 으로 오면 ①title_exclude_regex 가
    조용히 빗나가고 ②제외 결과에 따라 oldest_first 서수(회차 번호)까지 밀린다. 원어로 고정해야
    회차 번호가 실행 간 안정적이다(B급 스튜디오 263건 중 실측: 무지정이면 청문회 179건 중 86건만 매칭)."""
    cmd = [gen_py, "-m", "yt_dlp", "--flat-playlist", "--no-warnings"]
    if lang:
        cmd += ["--extractor-args", f"youtube:lang={lang}"]
    cmd += ["--print", "%(duration)s\t%(id)s\t%(title)s", source_url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"yt-dlp 채널 목록 실패 rc={r.returncode}: {(r.stderr or '')[-300:]}")
    return parse_index_lines(r.stdout)


def get_youtube_index(gen_py, source_url, cache_hours, log, now=None, lang=DEFAULT_YT_LANG):
    """캐시된 채널 인덱스(없거나 오래되면 갱신). 캐시는 results/youtube_index/<해시>.json.
    캐시 키에 lang 이 들어간다 — 언어가 다르면 제목이 달라지므로 같은 URL이라도 다른 캐시다."""
    p = _yt_index_cache_path(source_url, lang)
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
    entries = fetch_youtube_index(gen_py, source_url, lang=lang)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"source_url": source_url, "lang": lang,
                             "fetched_at": now.isoformat(timespec="seconds"),
                             "entries": entries}, ensure_ascii=False), encoding="utf-8")
    log(f"  [youtube] 인덱스 {len(entries)}건 저장")
    return entries


def ordinal_episodes(entries, start_episode=1, min_duration_sec=0):
    """제목에 회차 표기가 없는 채널(예: 커리어데이)용 — **업로드 오래된 순서**를 서수 회차로 삼는다.
    flat 목록은 최신순이므로 뒤집는다. 순수.
    ⚠️ 서수는 목록 순서에 의존 — 채널에서 옛 영상이 삭제되면 번호가 밀린다(새 업로드는 뒤에
    붙으므로 영향 없음). min_duration_sec 필터는 번호 부여 **전**에 적용해 번호를 안정시킨다."""
    seq = []
    for e in reversed(entries):
        if min_duration_sec > 0:
            d = e.get("duration")
            if d is None or float(d) < min_duration_sec:
                continue
        seq.append(e)
    return {n: [e] for n, e in enumerate(seq, 1) if n >= start_episode}


def discover_episodes_youtube(gen_py, ch, cache_hours, log):
    """유튜브 채널/플레이리스트 → [(회차, watch_url)] 오름차순. 회차당 가장 긴 영상 1건.
    episode_order='oldest_first' 면 제목 회차 대신 업로드 오래된 순 서수를 회차로 쓴다."""
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
    entries = get_youtube_index(gen_py, url, cache_hours, log,
                                lang=ch.get("yt_dlp_lang", DEFAULT_YT_LANG))
    before = len(entries)
    entries = exclude_entries(entries, ch.get("title_exclude_regex"))
    if len(entries) != before:
        log(f"  [youtube] 제외 규칙(title_exclude_regex)으로 {before - len(entries)}건 제외 "
            f"→ {len(entries)}건")
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
    """outputs* 에서 input.video_path==video_path 인 edit_plan → [{'span','run_id'}]."""
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


def rendered_scenes(state, channel, ep_num, video_path, scan_roots, iou_th, center_tol,
                    youtube=False):
    """이 (채널,회차)의 '서로 다른 렌더 장면' 목록 [{'span','run_ids'}] — 상태 + 기존 산출물 병합."""
    st = (((state.get("channels") or {}).get(channel) or {}).get("episodes") or {}) \
        .get(str(ep_num), {}).get("scenes", [])
    raw = [{"span": s["span"], "run_id": s.get("run_id")} for s in st if s.get("span")]
    raw += (episode_output_scenes(scan_roots, channel, ep_num) if youtube
            else existing_output_scenes(scan_roots, video_path))
    return merge_scenes(raw, iou_th, center_tol)


def record_scene(state, slot, channel, work_title, ep_num, video_path, span, run_id, job_dir):
    """상태에 확정 장면을 적는다. 키는 **슬롯**(slot_key) — 한 채널이 작품을 순차 소비할 때 분리된다.
    발행측(scene_publish_loop)이 업로드 대상 채널을 알아야 하므로 실제 채널명을 슬롯 안에 함께 적는다."""
    ch = state.setdefault("channels", {}).setdefault(
        slot, {"channel": channel, "work_title": work_title, "episodes": {}})
    ch.setdefault("channel", channel)          # 슬롯 도입 전 상태와의 호환(채널명=슬롯명)
    ep = ch.setdefault("episodes", {}).setdefault(str(ep_num), {"video_path": video_path, "scenes": []})
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


def youtube_public_ids(video_ids, api_key):
    """video_ids 중 유튜브에서 privacyStatus=='public' 인 것들의 집합. 공개 API 키로 조회
       (private 는 조회에서 아예 안 나옴 → 자동 제외). 실패 시 예외."""
    pub = set()
    ids = [v for v in dict.fromkeys(video_ids) if v]
    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        q = urllib.parse.urlencode({"part": "status", "id": ",".join(batch), "key": api_key})
        with urllib.request.urlopen("https://www.googleapis.com/youtube/v3/videos?" + q, timeout=20) as r:
            data = json.loads(r.read())
        for it in data.get("items", []):
            if (it.get("status") or {}).get("privacyStatus") == "public":
                pub.add(it["id"])
    return pub


def count_public_scenes(scenes, conn, channel, api_key):
    """scenes(merge_scenes 결과) 중 '공개된 장면' 수. 장면의 영상 중 하나라도 public 이면 공개."""
    run_ids = sorted({r for sc in scenes for r in sc["run_ids"]})
    run2vid = db_run_videos(conn, channel, run_ids)
    all_vids = [v for vs in run2vid.values() for v in vs]
    pub = youtube_public_ids(all_vids, api_key) if all_vids else set()
    cnt = 0
    for sc in scenes:
        vids = [v for r in sc["run_ids"] for v in run2vid.get(r, [])]
        if any(v in pub for v in vids):
            cnt += 1
    return cnt


# ─────────────────────────── 생성 (ai-video 그대로 호출) ───────────────────────────

def build_cmd(gen_py, work_title, video_path, outdir, gen_flags):
    """소스가 URL 이면 --youtube-url(ai-video 가 직접 받아 씀), 아니면 --video. 순수."""
    src = ["--youtube-url", video_path] if is_url(video_path) else ["--video", video_path]
    return [gen_py, "-m", "app.cli", "create_shorts",
            "--title", work_title, *src,
            "--max-shorts", "1", "--no-research", "--outdir", outdir, *gen_flags]


def run_generation(cmd, worktree, ai_video_root, timeout):
    env = dict(os.environ, PYTHONPATH=worktree, AI_VIDEO_ROOT=ai_video_root)
    return subprocess.run(cmd, cwd=worktree, env=env, capture_output=True, text=True, timeout=timeout)


def newest_job_dir(outdir):
    cands = glob.glob(str(Path(outdir) / "*" / "edit_plan.json"))
    return str(Path(max(cands, key=os.path.getmtime)).parent) if cands else None


# ─────────────────────────── 채널 상태 판정 ───────────────────────────

def channel_gen_flags(cfg, ch):
    """이 채널에 쓸 생성 플래그 — 전역 gen_flags + 채널별 gen_flags_extra.

    노브(silence/length/loudness)는 A/B 비교를 위해 전역이 정본이라 채널이 갈아엎지 못하게 하고,
    작품 사정에 따른 추가 플래그(자막 미제공분의 --no-subtitles 등)만 채널에서 덧붙인다."""
    return list(cfg.get("gen_flags", [])) + list(ch.get("gen_flags_extra", []))


def channel_quota(cfg, ch):
    """회차당 목표 장면 수 — 채널별 오버라이드(예: 커리어데이 영상당 2개) > 전역."""
    return int(ch.get("quota_per_episode", cfg["quota_per_episode"]))


def channel_max_pending(cfg, ch, quota):
    """미공개 대기 상한 — 채널별 > 전역. 전역이 채널 쿼터보다 크면 쿼터로 자른다
    (자동발행 운영에선 대기분이 전부 공개될 것이므로 쿼터 초과 생성은 낭비)."""
    if "max_pending_unpublished" in ch:
        return int(ch["max_pending_unpublished"])
    return min(int(cfg.get("max_pending_unpublished", quota)), quota)


def channel_plan(cfg, ch, state, conn, api_key, scan_roots, gen_py=None, log=lambda m: None):
    """이 채널이 지금 무엇을 할지 판정.
    반환 (action, ep_num, vp, info) — action ∈ {'gen','wait_publish','done_all','no_source'}."""
    quota = channel_quota(cfg, ch)
    max_pending = channel_max_pending(cfg, ch, quota)
    iou_th, ctol = cfg["dup_iou_threshold"], cfg["dup_center_tolerance_sec"]
    is_yt = channel_source_type(ch) == "youtube"
    eps = discover_episodes_for(ch, gen_py, cfg.get("youtube_index_cache_hours", 24), log)
    if not eps:
        return ("no_source", None, None, {"eps": []})
    for ep_num, vp in eps:
        scenes = rendered_scenes(state, slot_key(ch), ep_num, vp, scan_roots, iou_th, ctol,
                                 youtube=is_yt)
        pub = count_public_scenes(scenes, conn, ch["channel"], api_key)
        info = {"eps": eps, "rendered": len(scenes), "public": pub,
                "pending": len(scenes) - pub, "scenes": scenes}
        if pub >= quota:
            continue                                  # 이 회차 공개 완료 → 다음 회차 검사
        if info["pending"] >= max_pending:
            return ("wait_publish", ep_num, vp, info)  # 대기분 가득 → 공개 대기
        if info["rendered"] >= quota:
            return ("wait_publish", ep_num, vp, info)  # 렌더 총량이 쿼터 도달 — 공개만 남음
        return ("gen", ep_num, vp, info)               # 생성 필요
    return ("done_all", None, None, {"eps": eps})


# ─────────────────────────── 채널 1회 처리 ───────────────────────────

def process_channel(cfg, ch, state, conn, api_key, gen_py, worktree, ai_video_root, dry_run, log):
    """채널 1회 처리(최대 1장면 생성). 반환: 새 장면을 확정했으면 True — 호출측이
    per_run_scenes_per_channel 만큼 반복 호출하고, False 면 더 돌 게 없다는 뜻이라 멈춘다."""
    quota = channel_quota(cfg, ch)
    scan_roots = [str(Path(ai_video_root) / d) for d in cfg.get("outputs_scan_dirs", ["outputs"])]
    slot = slot_key(ch)
    tag = f"[{ch['channel']} · {ch['work_title']}]"
    try:
        action, ep_num, vp, info = channel_plan(cfg, ch, state, conn, api_key, scan_roots,
                                                gen_py, log)
    except urllib.error.URLError as e:
        log(f"{tag} ⚠ 유튜브 공개상태 조회 실패({e}) → 오늘 이 채널 스킵(오판 방지)")
        return False
    except Exception as e:  # noqa: BLE001 — DB 등 조회 실패
        log(f"{tag} ⚠ 공개 카운트 조회 실패({type(e).__name__}: {e}) → 오늘 이 채널 스킵")
        return False

    if action == "no_source":
        where = source_url_of(ch) if channel_source_type(ch) == "youtube" else ch.get("source_dir")
        log(f"{tag} 쓸 수 있는 회차 없음 → 스킵 ({where})")
        return False
    if action == "done_all":
        log(f"{tag} 발견된 회차({[e for e,_ in info['eps']]}) 모두 공개 {quota}개 충족 → 다음 회차 소스 대기")
        return False
    if action == "wait_publish":
        log(f"{tag} EP{ep_num}: 공개 {info['public']}/{quota}, 렌더 {info['rendered']}, "
            f"미공개 대기 {info['pending']}개(상한 {channel_max_pending(cfg, ch, quota)}) → 생성 멈춤. 공개 필요")
        return False

    # action == 'gen'
    log(f"{tag} EP{ep_num}: 공개 {info['public']}/{quota} (렌더 {info['rendered']}, 미공개 {info['pending']})"
        f" → 이번에 1장면 생성 (소스 {source_label(vp)})")
    if dry_run:
        log(f"{tag}   (dry-run) 생성 생략")
        return False

    iou_th, ctol = cfg["dup_iou_threshold"], cfg["dup_center_tolerance_sec"]
    attempts = 1 + cfg["max_retries"]
    prior_spans = [sc["span"] for sc in info["scenes"]]
    for attempt in range(1, attempts + 1):
        outdir = str(Path(ai_video_root) / "outputs" / "scene_loop" / slot /
                     episode_dir_name(ep_num) / f"try{attempt}_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        Path(outdir).mkdir(parents=True, exist_ok=True)
        cmd = build_cmd(gen_py, ch["work_title"], vp, outdir, channel_gen_flags(cfg, ch))
        log(f"{tag}   시도 {attempt}/{attempts}: {' '.join(cmd[:6])} … → {outdir}")
        try:
            r = run_generation(cmd, worktree, ai_video_root, cfg["gen_timeout_sec"])
        except subprocess.TimeoutExpired:
            log(f"{tag}   ✗ 생성 타임아웃({cfg['gen_timeout_sec']}s) → 이 채널 오늘 종료")
            return False
        job_dir = newest_job_dir(outdir)
        if r.returncode != 0 or not job_dir:
            log(f"{tag}   ✗ 생성 실패 rc={r.returncode} → 이 채널 오늘 종료. stderr꼬리: "
                f"{(r.stderr or r.stdout or '')[-300:]}")
            return False
        try:
            plan = json.loads((Path(job_dir) / "edit_plan.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log(f"{tag}   ✗ edit_plan 읽기 실패({e}) → 이 채널 오늘 종료")
            return False
        span = scene_span(plan)
        if span is None:
            log(f"{tag}   ✗ 장면 구간 없음(timeline 비어있음) → 이 채널 오늘 종료 (job={job_dir})")
            return False
        if is_duplicate(span, prior_spans, iou_th, ctol):
            log(f"{tag}   ↻ 중복 장면 {span} (기존과 겹침) → 재생성")
            continue
        run_id = _run_id_of(job_dir)
        record_scene(state, slot, ch["channel"], ch["work_title"], ep_num, vp, span, run_id, job_dir)
        save_state(state)
        log(f"{tag}   ✓ 새 장면 확정(미공개) {span} (run={run_id}) — 공개 {info['public']}/{quota} 유지."
            f" 공개 처리하면 회차 카운트 반영")
        return True
    log(f"{tag}   ⚠ {attempts}회 모두 이전과 같은 장면 → 보류(미확정). 다음날 재시도. 수동 확인 권장 (EP{ep_num})")
    return False


# ─────────────────────────── status ───────────────────────────

def cmd_status(cfg, state, conn, api_key, ai_video_root, log, gen_py=None):
    scan_roots = [str(Path(ai_video_root) / d) for d in cfg.get("outputs_scan_dirs", ["outputs"])]
    iou_th, ctol = cfg["dup_iou_threshold"], cfg["dup_center_tolerance_sec"]
    for ch in cfg["channels"]:
        quota = channel_quota(cfg, ch)
        is_yt = channel_source_type(ch) == "youtube"
        if is_queued(ch):
            log(f"[{ch['channel']} · {ch['work_title']}]  ⏸ 순차 대기(queued) — 앞 작품 종료 후 전환")
            continue
        eps = discover_episodes_for(ch, gen_py, cfg.get("youtube_index_cache_hours", 24), log)
        log(f"[{ch['channel']} · {ch['work_title']}]  회차: {[e for e,_ in eps] or '없음'}")
        for ep_num, vp in eps:
            scenes = rendered_scenes(state, slot_key(ch), ep_num, vp, scan_roots, iou_th, ctol,
                                     youtube=is_yt)
            try:
                pub = count_public_scenes(scenes, conn, ch["channel"], api_key)
                pubs = str(pub)
            except Exception as e:  # noqa: BLE001
                pub, pubs = None, f"조회실패({type(e).__name__})"
            mark = "✓완료" if (pub is not None and pub >= quota) else "…진행"
            log(f"    EP{ep_num}: 공개 {pubs}/{quota} {mark}  (렌더 {len(scenes)}, "
                f"미공개 {len(scenes)-pub if pub is not None else '?'})  "
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
    ap.add_argument("--config", default=str(CONFIG_PATH))
    a = ap.parse_args()

    cfg = load_config(a.config)
    state = load_state()

    def log(m):
        print(m, flush=True)

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
        channels = cfg["channels"]
        if a.channel:
            channels = [c for c in channels if a.channel in (c["channel"], slot_key(c))]
            if not channels:
                sys.exit(f"채널 '{a.channel}' 매니페스트에 없음")
        for ch in channels:
            if is_queued(ch):
                log(f"[{ch['channel']} · {ch['work_title']}] ⏸ 순차 대기(queued) — 건너뜀")
                continue
            # 채널별 1회 실행 생성 수 — 오버라이드 > 전역(기본 1). 각 반복은 상태를 다시
            # 판정하므로 쿼터/대기 상한에 닿으면 process_channel 이 False 를 돌려 자연 중단.
            runs = int(ch.get("per_run_scenes_per_channel",
                              cfg.get("per_run_scenes_per_channel", 1)))
            for i in range(max(1, runs)):
                try:
                    made = process_channel(cfg, ch, state, conn, api_key, gen_py, worktree,
                                           ai_video_root, a.dry_run, log)
                except Exception as e:  # noqa: BLE001 — 한 채널 실패가 다른 채널을 막지 않게
                    log(f"[{ch['channel']}] 처리 중 예외: {type(e).__name__}: {e}")
                    break
                if not made:
                    break
        log("=== scene_loop 종료 ===")
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()
