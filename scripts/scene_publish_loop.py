#!/usr/bin/env python3
"""scene_publish_loop — scene_loop 산출물 자동 발행 + '하루 N개 공개' 예약 루프.

scene_loop.py(생성)의 뒤를 이어 매일 1회 실행:
  ① 미발행 장면: ingest(ingest_aivideo_run.py) → 안전 judge(run_judge.py) →
     publish_youtube.py 로 업로드(안전게이트·오채널 차단은 거기 로직 그대로).
     공개 일정은 **유튜브 네이티브 예약(publishAt)** 으로 업로드 시점에 박는다:
     채널별 publish_times(예: ["19:00"])의 빈 슬롯 중 가장 이른 것 — private 로 올라가
     그 시각에 유튜브가 알아서 public 전환한다.
     (⚠️ 토큰이 upload 스코프뿐이라 사후 videos.update 공개 전환은 403 — 2026-07-27 실측.
      그래서 전환이 아니라 업로드 시점 예약으로 해결. 앱/맥이 꺼져 있어도 공개는 진행된다.)
  ② 예약 현황 보고: 채널별 예약 큐 + 사람이 처리할 것(수동 공개 필요분·차단분).

scene_loop 의 count_mode='public' 과 맞물려: 공개된 장면만 회차 카운트에 들어가므로
'회차당 quota 채우면 다음 회차'의 진행 속도를 공개 속도(하루 N슬롯)가 조절한다.

상태: results/scene_publish_state.json — {"scenes": {run_id: {...}}}
설정: config/scene_loop.json 채널 항목 "publish_times"(기본 ["19:00"] = 하루 1개),
      "publish_privacy" 를 "unlisted"/"private" 로 명시하면 예약 없이 그 상태로만 올린다.
env(.env 자동 로드): PIPELINE_DB_URL, GEMINI_API_KEY, YT_CLIENT_*, YT_REFRESH_TOKEN_*, REACT_APP_YOUTUBE_API_KEY

실행:
  .venv/bin/python scripts/scene_publish_loop.py             # 발행(예약 포함)+현황
  .venv/bin/python scripts/scene_publish_loop.py --dry-run   # 무엇을 할지만 출력
  .venv/bin/python scripts/scene_publish_loop.py --skip-publish   # 현황 보고만
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from envload import load_env  # noqa: E402
from scene_loop import slot_key  # noqa: E402  — 슬롯 규칙은 생성측이 정본

BRAIN = Path(__file__).resolve().parent.parent
CONFIG_PATH = BRAIN / "config" / "scene_loop.json"
GEN_STATE_PATH = BRAIN / "results" / "scene_loop_state.json"
PUB_STATE_PATH = BRAIN / "results" / "scene_publish_state.json"
PY = str(BRAIN / ".venv" / "bin" / "python")


# ─────────────────────────── 상태 ───────────────────────────

def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_pub_state(st):
    PUB_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PUB_STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


# ─────────────────────────── 발행 대상 수집 ───────────────────────────

def pending_scenes(gen_state, pub_state):
    """scene_loop 상태 → 아직 발행 안 된 장면 [(슬롯, 채널, ep, scene)] (accepted_at 오래된 순).

    상태의 최상위 키는 **슬롯**이다(한 채널이 작품을 순차 소비하면 채널명과 달라진다). 업로드 대상
    채널은 슬롯 안의 'channel' 이 정본 — 없으면(슬롯 도입 전 상태) 슬롯명이 곧 채널명이다."""
    done = pub_state.setdefault("scenes", {})
    out = []
    for slot, ch in (gen_state.get("channels") or {}).items():
        ch_name = ch.get("channel") or slot
        for ep_num, ep in (ch.get("episodes") or {}).items():
            for sc in ep.get("scenes", []):
                rid = sc.get("run_id")
                if not rid:
                    continue
                stage = (done.get(rid) or {}).get("stage")
                if stage in ("published", "blocked"):
                    continue
                out.append((slot, ch_name, int(ep_num), sc))
    out.sort(key=lambda t: t[3].get("accepted_at") or "")
    return out


def find_video(job_dir):
    """job_dir → 최종 쇼츠 mp4. ai-video 규약은 shorts.mp4, 폴백은 가장 큰 mp4."""
    p = Path(job_dir) / "shorts.mp4"
    if p.exists():
        return str(p)
    cands = [q for q in Path(job_dir).glob("*.mp4") if "_source" not in q.name]
    return str(max(cands, key=lambda q: q.stat().st_size)) if cands else None


def sh(cmd, timeout=3600):
    """서브프로세스 실행 → (rc, stdout+stderr 합침)."""
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(BRAIN))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# ─────────────────────────── ① 발행 (ingest → judge → upload) ───────────────────────────

def next_publish_slot(ch_cfg, pub_state, ch_name, now=None):
    """채널의 다음 빈 공개 슬롯(datetime, tz-aware) — publish_times(하루 N개) 기준.
    이미 예약된 장면들(scheduled_publish_at)이 채운 날은 다음 시각/다음 날로 넘어간다."""
    from datetime import timedelta
    times = ch_cfg.get("publish_times") or ["19:00"]
    now = now or datetime.now().astimezone()
    taken = {}
    for r in (pub_state.get("scenes") or {}).values():
        if r.get("channel") == ch_name and r.get("scheduled_publish_at"):
            d = r["scheduled_publish_at"][:10]
            taken[d] = taken.get(d, 0) + 1
    for dayoff in range(365):
        day = (now + timedelta(days=dayoff)).date()
        used = taken.get(day.isoformat(), 0)
        for idx in range(used, len(times)):
            hh, mm = (int(x) for x in times[idx].split(":"))
            slot = datetime(day.year, day.month, day.day, hh, mm).astimezone()
            if slot > now:
                return slot
    return None


def title_override(ch_cfg, clip_id, log):
    """권리사 가이드가 '제목에 작품 제목 삽입'을 요구하는 채널(require_work_in_title=true)이면
    DB 제목에 작품명이 없을 때 '<제목> | <작품명>' 으로 강제한다. 아니면 None(=DB 제목 그대로)."""
    if not ch_cfg.get("require_work_in_title"):
        return None
    wt = ch_cfg.get("work_title") or ""
    if not wt:
        return None
    try:
        import psycopg
        import publish_youtube as pub
        with psycopg.connect(os.environ["PIPELINE_DB_URL"]) as conn:
            db_title, _ = pub.fetch_clip_title(conn, clip_id)
    except Exception as e:  # noqa: BLE001
        log(f"  ⚠ 제목 조회 실패({type(e).__name__}) — 작품명 삽입 확인 불가, DB 제목 사용")
        return None
    if db_title and wt.replace(" ", "") not in db_title.replace(" ", ""):
        return f"{db_title} | {wt}"
    return None


def publish_scene(ch_name, ep_num, sc, rec, log, dry_run, ch_cfg=None, pub_state=None):
    """장면 1개: ingest→judge→업로드(예약 공개). rec(dict)를 단계별로 채워 리턴(멱등 재시도 가능)."""
    rid = sc["run_id"]
    ch_cfg = ch_cfg or {}
    job_dir = sc.get("job_dir")
    video = find_video(job_dir) if job_dir else None
    if not video:
        log(f"  ✗ {rid}: 영상 파일 없음 (job_dir={job_dir}) → 건너뜀")
        return rec
    tag = f"[{ch_name} EP{ep_num} run={rid[:12]}]"
    # 고정 privacy 명시(publish_privacy) 없으면 예약 공개(publishAt) 슬롯을 잡는다
    fixed_privacy = ch_cfg.get("publish_privacy")
    slot = None if fixed_privacy else next_publish_slot(ch_cfg, pub_state or {}, ch_name)
    if dry_run:
        when = fixed_privacy or (slot and f"예약공개 {slot.isoformat(timespec='minutes')}")
        log(f"  (dry-run) {tag} ingest→judge→upload({when}) 예정 (video={Path(video).name})")
        return rec

    # ingest (멱등 — 재실행 시 updated)
    if not rec.get("clip_id"):
        rc, out = sh([PY, "scripts/ingest_aivideo_run.py", "--run-dir", job_dir,
                      "--short-label", "shorts_1", "--channel", ch_name])
        m = re.search(r"(?:inserted|updated) clip ([0-9a-f-]{36})", out)
        if rc != 0 or not m:
            log(f"  ✗ {tag} ingest 실패 rc={rc}: {out[-300:]}")
            return rec
        rec["clip_id"] = m.group(1)
        log(f"  ✓ {tag} ingest → clip {rec['clip_id'][:8]}…")

    # 안전 judge (발행 게이트 전제조건)
    if rec.get("stage") not in ("judged", "published") :
        rc, out = sh([PY, "scripts/run_judge.py", "--clip-id", rec["clip_id"], "--video", video],
                     timeout=1800)
        if rc != 0:
            log(f"  ✗ {tag} judge 실패 rc={rc}: {out[-300:]}")
            return rec
        rec["stage"] = "judged"
        log(f"  ✓ {tag} judge 완료")

    # 업로드 (publish_youtube 가 안전게이트·오채널·지오블락 게이트 수행)
    cmd = [PY, "scripts/publish_youtube.py", "--clip-id", rec["clip_id"], "--video", video,
           "--channel", ch_name, "--episode", str(ep_num), "--publish"]
    if fixed_privacy:
        cmd += ["--privacy", fixed_privacy]
    elif slot:
        cmd += ["--privacy", "private", "--publish-at", slot.isoformat()]
    to = title_override(ch_cfg, rec["clip_id"], log)
    if to:
        cmd += ["--title", to]
        log(f"  ℹ {tag} 제목에 작품명 삽입(가이드): {to!r}")
    rc, out = sh(cmd)
    m = re.search(r"uploaded content_id:\s*(\S+)", out)
    if m:
        rec.update(stage="published", video_id=m.group(1),
                   privacy=(fixed_privacy or "scheduled"),
                   published_at=datetime.now().isoformat(timespec="seconds"))
        if slot and not fixed_privacy:
            rec["scheduled_publish_at"] = slot.isoformat()
            log(f"  ✓ {tag} 업로드 → https://youtu.be/{rec['video_id']} "
                f"(공개 예약 {slot.isoformat(timespec='minutes')})")
        else:
            log(f"  ✓ {tag} 업로드({fixed_privacy}) → https://youtu.be/{rec['video_id']}")
    elif "게이트 차단" in out or "지오블락 게이트 차단" in out:
        rec["stage"] = "blocked"
        rec["blocked_reason"] = out[-300:].strip()
        log(f"  ⛔ {tag} 게이트 차단(발행 안 함) — 사람 확인 필요: {out[-200:]}")
    else:
        log(f"  ✗ {tag} 업로드 실패 rc={rc}: {out[-300:]}")
    return rec


# ─────────────────────────── ② 예약 현황 보고 ───────────────────────────

def report_schedule(cfg, pub_state, log):
    """채널별 공개 예약 큐 + 사람이 처리할 것(수동 공개 필요분·게이트 차단분)을 로그로 보고."""
    scenes = pub_state.get("scenes", {})
    now_iso = datetime.now().astimezone().isoformat()
    seen = set()
    for ch in cfg["channels"]:
        name = ch["channel"]
        if name in seen:      # 같은 채널에 작품이 둘(순차) — 공개 슬롯은 채널 단위라 한 번만 보고
            continue
        seen.add(name)
        rows = [(rid, r) for rid, r in scenes.items() if r.get("channel") == name]
        sched = sorted((r["scheduled_publish_at"], r["video_id"]) for _, r in rows
                       if r.get("scheduled_publish_at") and r.get("video_id"))
        upcoming = [(t, v) for t, v in sched if t > now_iso]
        manual = [r["video_id"] for _, r in rows
                  if r.get("privacy") == "unlisted" and r.get("video_id")]
        blocked = [rid for rid, r in rows if r.get("stage") == "blocked"]
        parts = [f"공개 예약 대기 {len(upcoming)}건"]
        if upcoming:
            parts.append("다음: " + ", ".join(f"{t[:16]} youtu.be/{v}" for t, v in upcoming[:3]))
        if manual:
            parts.append(f"⚠ 수동 공개 필요(unlisted, 토큰 스코프 한계로 API 전환 불가): "
                         + ", ".join(f"youtu.be/{v}" for v in manual))
        if blocked:
            parts.append(f"⛔ 게이트 차단 {len(blocked)}건(사람 확인)")
        log(f"[예약 현황] {name}: " + " | ".join(parts))


# ─────────────────────────── main ───────────────────────────

def main():
    load_env()
    ap = argparse.ArgumentParser(description="scene_loop 산출물 자동 발행 + 하루 N개 공개 전환")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-publish", action="store_true", help="공개 전환만 수행")
    ap.add_argument("--config", default=str(CONFIG_PATH))
    a = ap.parse_args()

    def log(m):
        print(m, flush=True)

    cfg = load_json(a.config, None)
    if not cfg:
        sys.exit(f"config 없음: {a.config}")
    gen_state = load_json(GEN_STATE_PATH, {})
    pub_state = load_json(PUB_STATE_PATH, {})
    pub_state.setdefault("scenes", {})

    log(f"=== scene_publish_loop {datetime.now().isoformat(timespec='seconds')} dry_run={a.dry_run} ===")

    if not a.skip_publish:
        todo = pending_scenes(gen_state, pub_state)
        log(f"[발행] 미발행 장면 {len(todo)}건")
        # 설정은 슬롯으로 찾는다(같은 채널에 작품이 둘이면 채널명만으론 항목이 겹친다).
        # 발행 파라미터(--channel·공개 슬롯)는 항상 실제 채널명을 쓴다.
        ch_cfgs = {slot_key(c): c for c in cfg["channels"]}
        own_channels = {c["channel"] for c in cfg["channels"]}
        for slot, ch_name, ep_num, sc in todo:
            rid = sc["run_id"]
            # ⚠️ 상태 파일에는 이 머신이 더는 담당하지 않는 채널의 옛 장면이 남아 있을 수 있다
            # (담당 재배정 전에 만든 것). 설정에 없는 채널로는 절대 발행하지 않는다 — 남의 채널에
            # 올라가는 사고를 막는다. 상태를 지우지 않는 건 발행 이력을 보존하기 위해서다.
            if slot not in ch_cfgs and ch_name not in own_channels:
                log(f"  ⏭ [{ch_name} EP{ep_num} run={rid[:12]}] 이 머신 담당 채널이 아님"
                    f"(config/scene_loop.json 에 없음) → 발행 건너뜀")
                continue
            ch_cfg = ch_cfgs.get(slot) or ch_cfgs.get(ch_name, {})
            rec = pub_state["scenes"].setdefault(rid, {"channel": ch_name, "slot": slot,
                                                       "episode": ep_num})
            rec.setdefault("channel", ch_name)
            publish_scene(ch_name, ep_num, sc, rec, log, a.dry_run, ch_cfg, pub_state)
            if not a.dry_run:
                save_pub_state(pub_state)

    report_schedule(cfg, pub_state, log)
    if not a.dry_run:
        save_pub_state(pub_state)
    log("=== scene_publish_loop 종료 ===")


if __name__ == "__main__":
    main()
