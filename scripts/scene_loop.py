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


def rendered_scenes(state, channel, ep_num, video_path, scan_roots, iou_th, center_tol):
    """이 (채널,회차)의 '서로 다른 렌더 장면' 목록 [{'span','run_ids'}] — 상태 + 기존 산출물 병합."""
    st = (((state.get("channels") or {}).get(channel) or {}).get("episodes") or {}) \
        .get(str(ep_num), {}).get("scenes", [])
    raw = [{"span": s["span"], "run_id": s.get("run_id")} for s in st if s.get("span")]
    raw += existing_output_scenes(scan_roots, video_path)
    return merge_scenes(raw, iou_th, center_tol)


def record_scene(state, channel, work_title, ep_num, video_path, span, run_id, job_dir):
    ch = state.setdefault("channels", {}).setdefault(channel, {"work_title": work_title, "episodes": {}})
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
    return [gen_py, "-m", "app.cli", "create_shorts",
            "--title", work_title, "--video", video_path,
            "--max-shorts", "1", "--no-research", "--outdir", outdir, *gen_flags]


def run_generation(cmd, worktree, ai_video_root, timeout):
    env = dict(os.environ, PYTHONPATH=worktree, AI_VIDEO_ROOT=ai_video_root)
    return subprocess.run(cmd, cwd=worktree, env=env, capture_output=True, text=True, timeout=timeout)


def newest_job_dir(outdir):
    cands = glob.glob(str(Path(outdir) / "*" / "edit_plan.json"))
    return str(Path(max(cands, key=os.path.getmtime)).parent) if cands else None


# ─────────────────────────── 채널 상태 판정 ───────────────────────────

def channel_plan(cfg, ch, state, conn, api_key, scan_roots):
    """이 채널이 지금 무엇을 할지 판정.
    반환 (action, ep_num, vp, info) — action ∈ {'gen','wait_publish','done_all','no_source'}."""
    quota = cfg["quota_per_episode"]
    max_pending = cfg.get("max_pending_unpublished", quota)
    iou_th, ctol = cfg["dup_iou_threshold"], cfg["dup_center_tolerance_sec"]
    eps = discover_episodes(ch["source_dir"], ch["video_glob"], ch["episode_regex"],
                            ch.get("start_episode", 1))
    if not eps:
        return ("no_source", None, None, {"eps": []})
    for ep_num, vp in eps:
        scenes = rendered_scenes(state, ch["channel"], ep_num, vp, scan_roots, iou_th, ctol)
        pub = count_public_scenes(scenes, conn, ch["channel"], api_key)
        info = {"eps": eps, "rendered": len(scenes), "public": pub,
                "pending": len(scenes) - pub, "scenes": scenes}
        if pub >= quota:
            continue                                  # 이 회차 공개 완료 → 다음 회차 검사
        if info["pending"] >= max_pending:
            return ("wait_publish", ep_num, vp, info)  # 대기분 가득 → 사람 공개 대기
        return ("gen", ep_num, vp, info)               # 생성 필요
    return ("done_all", None, None, {"eps": eps})


# ─────────────────────────── 채널 1회 처리 ───────────────────────────

def process_channel(cfg, ch, state, conn, api_key, gen_py, worktree, ai_video_root, dry_run, log):
    quota = cfg["quota_per_episode"]
    scan_roots = [str(Path(ai_video_root) / d) for d in cfg.get("outputs_scan_dirs", ["outputs"])]
    tag = f"[{ch['channel']} · {ch['work_title']}]"
    try:
        action, ep_num, vp, info = channel_plan(cfg, ch, state, conn, api_key, scan_roots)
    except urllib.error.URLError as e:
        log(f"{tag} ⚠ 유튜브 공개상태 조회 실패({e}) → 오늘 이 채널 스킵(오판 방지)")
        return
    except Exception as e:  # noqa: BLE001 — DB 등 조회 실패
        log(f"{tag} ⚠ 공개 카운트 조회 실패({type(e).__name__}: {e}) → 오늘 이 채널 스킵")
        return

    if action == "no_source":
        log(f"{tag} 소스 폴더에 회차 파일 없음 → 스킵 ({ch['source_dir']})")
        return
    if action == "done_all":
        log(f"{tag} 발견된 회차({[e for e,_ in info['eps']]}) 모두 공개 {quota}개 충족 → 다음 회차 소스 대기")
        return
    if action == "wait_publish":
        log(f"{tag} EP{ep_num}: 공개 {info['public']}/{quota}, 미공개 대기 {info['pending']}개"
            f"(상한 {cfg.get('max_pending_unpublished', quota)}) → 생성 멈춤. 리뷰/공개 필요")
        return

    # action == 'gen'
    log(f"{tag} EP{ep_num}: 공개 {info['public']}/{quota} (렌더 {info['rendered']}, 미공개 {info['pending']})"
        f" → 이번에 1장면 생성 (소스 {Path(vp).name})")
    if dry_run:
        log(f"{tag}   (dry-run) 생성 생략")
        return

    iou_th, ctol = cfg["dup_iou_threshold"], cfg["dup_center_tolerance_sec"]
    attempts = 1 + cfg["max_retries"]
    prior_spans = [sc["span"] for sc in info["scenes"]]
    for attempt in range(1, attempts + 1):
        outdir = str(Path(ai_video_root) / "outputs" / "scene_loop" / ch["channel"] /
                     f"ep{ep_num:02d}" / f"try{attempt}_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        Path(outdir).mkdir(parents=True, exist_ok=True)
        cmd = build_cmd(gen_py, ch["work_title"], vp, outdir, cfg["gen_flags"])
        log(f"{tag}   시도 {attempt}/{attempts}: {' '.join(cmd[:6])} … → {outdir}")
        try:
            r = run_generation(cmd, worktree, ai_video_root, cfg["gen_timeout_sec"])
        except subprocess.TimeoutExpired:
            log(f"{tag}   ✗ 생성 타임아웃({cfg['gen_timeout_sec']}s) → 이 채널 오늘 종료")
            return
        job_dir = newest_job_dir(outdir)
        if r.returncode != 0 or not job_dir:
            log(f"{tag}   ✗ 생성 실패 rc={r.returncode} → 이 채널 오늘 종료. stderr꼬리: "
                f"{(r.stderr or r.stdout or '')[-300:]}")
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
        record_scene(state, ch["channel"], ch["work_title"], ep_num, vp, span, run_id, job_dir)
        save_state(state)
        log(f"{tag}   ✓ 새 장면 확정(미공개) {span} (run={run_id}) — 공개 {info['public']}/{quota} 유지."
            f" 공개 처리하면 회차 카운트 반영")
        return
    log(f"{tag}   ⚠ {attempts}회 모두 이전과 같은 장면 → 보류(미확정). 다음날 재시도. 수동 확인 권장 (EP{ep_num})")


# ─────────────────────────── status ───────────────────────────

def cmd_status(cfg, state, conn, api_key, ai_video_root, log):
    scan_roots = [str(Path(ai_video_root) / d) for d in cfg.get("outputs_scan_dirs", ["outputs"])]
    quota = cfg["quota_per_episode"]
    iou_th, ctol = cfg["dup_iou_threshold"], cfg["dup_center_tolerance_sec"]
    for ch in cfg["channels"]:
        eps = discover_episodes(ch["source_dir"], ch["video_glob"], ch["episode_regex"],
                            ch.get("start_episode", 1))
        log(f"[{ch['channel']} · {ch['work_title']}]  회차 파일: {[e for e,_ in eps] or '없음'}")
        for ep_num, vp in eps:
            scenes = rendered_scenes(state, ch["channel"], ep_num, vp, scan_roots, iou_th, ctol)
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
            cmd_status(cfg, state, conn, api_key, ai_video_root, log)
            return
        if "GEMINI_API_KEY" not in os.environ and not a.dry_run:
            sys.exit("GEMINI_API_KEY 미설정 — .env 로드 실패? (생성 불가)")

        log(f"=== scene_loop {datetime.now().isoformat(timespec='seconds')} "
            f"| root={ai_video_root} | mode={cfg.get('count_mode')} | dry_run={a.dry_run} ===")
        channels = cfg["channels"]
        if a.channel:
            channels = [c for c in channels if c["channel"] == a.channel]
            if not channels:
                sys.exit(f"채널 '{a.channel}' 매니페스트에 없음")
        for ch in channels:
            try:
                process_channel(cfg, ch, state, conn, api_key, gen_py, worktree,
                                 ai_video_root, a.dry_run, log)
            except Exception as e:  # noqa: BLE001 — 한 채널 실패가 다른 채널을 막지 않게
                log(f"[{ch['channel']}] 처리 중 예외: {type(e).__name__}: {e}")
        log("=== scene_loop 종료 ===")
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()
