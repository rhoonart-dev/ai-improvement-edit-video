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

설정(2026-07-30 이식 시 정본 체계에 맞춤): 담당 채널은 **scene_loop 과 똑같이**
config/assignments.json 에서 해석한다(resolve_run_config) — 발행측이 별도 채널 목록을 들면
생성과 어긋나 남의 채널에 올라간다. 발행 파라미터는 두 곳에서 온다:
  - config/loop_policy.json  : publish_times(기본 ["19:00"] = 하루 1개) · publish_privacy 전역 기본
  - config/channels.json     : 채널 항목의 publish_times · publish_privacy · require_work_in_title 이
                               있으면 그 채널만 전역값을 덮는다
publish_privacy 를 "unlisted"/"private" 로 명시하면 예약 없이 그 상태로만 올린다.

env(.env 자동 로드): PIPELINE_DB_URL, GEMINI_API_KEY, YT_CLIENT_*, YT_REFRESH_TOKEN_*, REACT_APP_YOUTUBE_API_KEY

실행:
  .venv/bin/python scripts/scene_publish_loop.py             # 발행(예약 포함)+현황
  .venv/bin/python scripts/scene_publish_loop.py --dry-run   # 무엇을 할지만 출력
  .venv/bin/python scripts/scene_publish_loop.py --skip-publish   # 현황 보고만
  .venv/bin/python scripts/scene_publish_loop.py --channel 너굴안방  # 한 채널만
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
import channel_registry as registry  # noqa: E402
import scene_loop as sl  # noqa: E402  — 채널 해석은 생성측이 정본
from envload import load_env  # noqa: E402

BRAIN = Path(__file__).resolve().parent.parent
GEN_STATE_PATH = BRAIN / "results" / "scene_loop_state.json"
PUB_STATE_PATH = sl.PUB_STATE_PATH   # 경로 정본은 생성측 — 어긋나면 생성이 예약분을 반려로 본다
PY = str(BRAIN / ".venv" / "bin" / "python")

# 채널 항목이 전역 기본을 덮을 수 있는 발행 설정 키
PUBLISH_KEYS = ("publish_times", "publish_privacy", "require_work_in_title")


slot_key = sl.slot_key   # 진행 슬롯 규칙은 생성측이 정본(7fb0305 에서 도입)


def publish_config(ch_cfg, policy, records):
    """채널 dict + 전역 정책 + channels.json 레코드 → 발행 파라미터가 채워진 채널 설정.

    정본 체계에는 발행 키가 없다(_card_to_channel_config 는 생성에 필요한 것만 싣는다).
    전역 기본(loop_policy)을 깔고 채널 레코드(channels.json)가 덮는 순서로 합친다.
    """
    out = dict(ch_cfg)
    for k in PUBLISH_KEYS:
        if policy.get(k) is not None:
            out[k] = policy[k]
    rec = registry.resolve(ch_cfg["channel"], records) or {}
    for k in PUBLISH_KEYS:
        if rec.get(k) is not None:
            out[k] = rec[k]
    return out


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

def seed_published_from_db(gen_state, pub_state, log):
    """DB 에 이미 유튜브 영상이 링크된 장면을 '발행 완료'로 표시. 성공 여부를 bool 로 돌려준다.

    🛑 **pub_state 만 믿으면 같은 영상을 다시 올린다.** 이 루프를 붙이기 전에 사람이 손으로
    발행한 분은 pub_state 에 흔적이 없는데 유튜브에는 이미 올라가 있다. 2026-07-30 이식 당시
    첫 dry-run 이 18건을 미발행으로 집었는데 그중 3건은 이미 **공개된** 영상이었다.
    진실은 pub_state 가 아니라 DB 링크(clips.video_external_id)다 — 그쪽을 먼저 반영한다.

    비공개로 반려된 장면도 video_external_id 는 남아 있으므로 여기서 함께 걸러진다(재업로드 금지).
    """
    conn = sl._connect_db(log)
    if conn is None:
        return False
    n = 0
    try:
        for slot, ch in (gen_state.get("channels") or {}).items():
            ch_name = ch.get("channel") or slot
            rids = sorted({sc.get("run_id") for ep in (ch.get("episodes") or {}).values()
                           for sc in ep.get("scenes", []) if sc.get("run_id")})
            if not rids:
                continue
            for (rid, take), vids in sl.db_run_videos(conn, ch_name, rids).items():
                # 🛑 키는 반드시 state_key(rid, take) — run_id 로만 심으면 테이크2·3 의 '이미 발행됨'
                # 기록이 정본 자리에 들어가고, 정작 자기 자리는 빈 채로 남아 방어선이 죽는다.
                rec = pub_state.setdefault("scenes", {}).setdefault(state_key(rid, take), {})
                if rec.get("stage") in ("published", "blocked"):
                    continue
                rec.update(channel=ch_name, slot=slot, stage="published", take=take,
                           video_id=vids[0], source="db-reconciled")
                n += 1
    finally:
        conn.close()
    if n:
        log(f"[정합] DB 에 이미 링크된 발행분 {n}건을 발행 완료로 표시 — 중복 업로드 방지")
    return True


def pending_scenes(gen_state, pub_state):
    """scene_loop 상태 → 아직 발행 안 된 장면 [(슬롯, 채널, ep, scene)] (accepted_at 오래된 순).

    상태의 최상위 키는 **슬롯**이다(한 채널이 작품을 순차 소비하면 채널명과 달라진다). 업로드 대상
    채널은 슬롯 안의 'channel' 이 정본 — 없으면(슬롯 도입 전 상태) 슬롯명이 곧 채널명이다."""
    done = pub_state.setdefault("scenes", {})
    out = []
    known_channels = set(registry.channel_names())
    for slot, ch in (gen_state.get("channels") or {}).items():
        ch_name = ch.get("channel") or slot
        # 구 상태 폴백: 'channel' 필드가 없는 다작품 슬롯('채널·작품')은 슬롯명이 채널명이 아니다 —
        # 등록 채널명과 대조해 복원한다(2026-08-04 실측: 재미쇼츠 발행이 미등록 채널로 하드 실패).
        if ch_name not in known_channels and "·" in ch_name:
            head = ch_name.split("·")[0].strip()
            if head in known_channels:
                ch_name = head
        for ep_num, ep in (ch.get("episodes") or {}).items():
            for sc in ep.get("scenes", []):
                rid = sc.get("run_id")
                if not rid:
                    continue
                # 🛑 테이크별 키로 봐야 한다 — run_id 로만 보면 정본이 발행되는 순간 같은 job 의
                # 테이크2·3 이 목록에서 통째로 사라져 **합격작이 영영 안 나간다**(2026-08-07).
                stage = (done.get(state_key(rid, sc.get("take"))) or {}).get("stage")
                if stage in ("published", "blocked"):
                    continue
                out.append((slot, ch_name, int(ep_num), sc))
    out.sort(key=lambda t: t[3].get("accepted_at") or "")
    return out


REVIEW_GATE_PUBLISH = "publish"
REVIEW_GATE_HOLD = "hold"          # 결정 없음 — 검수 대기, 발행 보류
REVIEW_GATE_REJECT = "reject"      # 반려 — 발행 안 함 + 슬롯 즉시 해제


def review_gate(decision):
    """검수 결정 → 발행 게이트 (4단계, 2026-08-05 부터 '합격작만 발행').

    decision: fetch_review_decisions 의 행 (decision, decided_at, note) 또는 None.
    설계(DASHBOARD_REVIEW_STORAGE_DESIGN §6): approved→발행 · rejected→해제 · 없음→보류.
    """
    if decision is None:
        return REVIEW_GATE_HOLD
    return REVIEW_GATE_PUBLISH if decision[0] == "approved" else REVIEW_GATE_REJECT


# 상태 키 규약은 scene_loop 이 정본이다 — 두 루프가 **같은 상태 파일**(scene_publish_state.json)을
# 쓰기 때문에 규약이 갈리면 한쪽이 심은 기록을 다른 쪽이 못 찾는다(2026-08-07 실제 사고).
state_key = sl.state_key


def fetch_review_decisions(run_ids):
    """{(run_id, take): (decision, decided_at_iso, note, reject_type)} — 테이크별 검수 결정.

    🛑 예전에는 `c.episode = 'shorts_1'` 로 못 박고 run_id 로만 키를 잡았다. 그래서 변이 테이크가
    합격해도 조회에 안 잡혀 **영영 '검수 대기'로 발행이 보류됐다**(2026-08-06 실측: 맥3 명장면
    세탁소 shorts_2 · 맥2 스트릿 레스토랑 파이터 shorts_2). 안전 방향 실패라 잘못된 발행은 없었지만,
    합격작이 안 나가는 것도 사고다.

    실패 시 예외를 그대로 올린다 — 결정을 못 읽는 상태에서 발행하면 미검수분이 올라간다
    (seed_published_from_db 와 같은 '안전 방향으로만 실패' 원칙).
    """
    if not run_ids:
        return {}
    try:
        import psycopg as pg
    except ModuleNotFoundError:
        import psycopg2 as pg
    with pg.connect(os.environ["PIPELINE_DB_URL"], connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT m.ai_video_run_id, r.decision, r.decided_at, r.note, r.reject_type, c.episode
                   FROM public.review_decisions r
                   JOIN public.clips c ON c.id = r.clip_id
                   JOIN public.clip_metadata m ON m.clip_id = c.id
                   WHERE m.ai_video_run_id = ANY(%s) AND c.source = 'auto_edit'""",
                (list(run_ids),))
            return {(r[0], r[5] or "shorts_1"): (r[1], r[2].isoformat() if r[2] else None, r[3], r[4])
                    for r in cur.fetchall()}


def judge_run_exists(clip_id):
    """이 clip 에 judge_runs 행이 있는가 — 야간 선실행분 감지(재실행 = Gemini 비용 중복)."""
    try:
        import psycopg as pg
    except ModuleNotFoundError:
        import psycopg2 as pg
    with pg.connect(os.environ["PIPELINE_DB_URL"], connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM public.judge_runs WHERE clip_id = %s LIMIT 1", (clip_id,))
            return cur.fetchone() is not None


def take_files(job_dir, take):
    """라벨 → (video, edit_plan). 정본만 파일명(shorts.mp4)과 라벨(shorts_1)이 어긋난다.

    scene_loop.take_files 와 같은 규약이다 — --max-shorts 3 부터 한 job 이 테이크 3개를 내고
    장면마다 어느 것인지가 다르다. 🛑 이 값을 안 보면 테이크 2·3 자리에 **정본 영상이 발행된다.**"""
    p, n = Path(job_dir), str(take or "shorts_1")
    if n in ("shorts_1", "shorts"):
        return p / "shorts.mp4", p / "edit_plan.json"
    return p / f"{n}.mp4", p / f"edit_plan_{n.split('_')[-1]}.json"


def find_video(job_dir, take="shorts_1"):
    """job_dir(+테이크) → 쇼츠 mp4. 규약 이름이 없으면 폴백은 가장 큰 mp4."""
    p = take_files(job_dir, take)[0]
    if p.exists():
        return str(p)
    if str(take or "shorts_1") not in ("shorts_1", "shorts"):
        return None     # 변이는 폴백하지 않는다 — 없는데 정본을 올리면 다른 영상이 나간다
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
            if dayoff == 0:
                # 오늘 몫이 비었는데 슬롯 시각만 지난 경우 — 내일로 밀지 않고 지금 낸다
                # (2026-08-05 운영자 결정: "저녁 늦게 검수해도 날이 안 지났으면 그 순간 공개").
                # 과거 시각 예약은 유튜브가 거부하므로 지금+5분 근미래 예약으로 올린다.
                # 하루 상한은 그대로다 — 이 즉시분도 taken 에 잡혀 다음 합격은 내일로 간다.
                return now + timedelta(minutes=5)
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
    take = sc.get("take") or "shorts_1"
    video = find_video(job_dir, take) if job_dir else None
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
        if slot and not fixed_privacy:
            # dry-run 에서도 슬롯을 점유한 것으로 쳐야 다음 장면이 같은 시각으로 찍히지 않는다.
            # (실제 실행은 장면마다 상태를 저장하므로 자연히 다음 슬롯으로 넘어간다.)
            rec["channel"] = ch_name
            rec["scheduled_publish_at"] = slot.isoformat()
        return rec

    # ingest (멱등 — 재실행 시 updated)
    if not rec.get("clip_id"):
        _plan = take_files(job_dir, take)[1]
        _extra = ["--edit-plan", str(_plan)] if take not in ("shorts_1", "shorts") else []
        rc, out = sh([PY, "scripts/ingest_aivideo_run.py", "--run-dir", job_dir,
                      "--short-label", take, "--channel", ch_name, *_extra])
        m = re.search(r"(?:inserted|updated) clip ([0-9a-f-]{36})", out)
        if rc != 0 or not m:
            log(f"  ✗ {tag} ingest 실패 rc={rc}: {out[-300:]}")
            return rec
        rec["clip_id"] = m.group(1)
        log(f"  ✓ {tag} ingest → clip {rec['clip_id'][:8]}…")

    # 안전 judge (발행 게이트 전제조건). 2026-08-05 부터 야간 업로더가 검수 전에 선실행하므로
    # DB 에 이미 있으면 생략한다 — rec['stage'] 는 이 머신 상태 파일이라 선실행을 모른다.
    if rec.get("stage") not in ("judged", "published") :
        already = False
        try:
            already = judge_run_exists(rec["clip_id"])
        except Exception as e:  # noqa: BLE001 — 조회 실패면 안전 방향 = 재실행
            log(f"  ⚠ {tag} judge 존재 조회 실패({type(e).__name__}) → 재실행으로 진행")
        if already:
            rec["stage"] = "judged"
            log(f"  ✓ {tag} judge 있음(검수 전 선실행분) → 재실행 생략")
        else:
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
    ap.add_argument("--channel", default=None, help="이 채널만 처리")
    ap.add_argument("--machine", default=None, help="배정 정본에서 쓸 머신 id(기본: 자동 감지)")
    # 아래 둘은 시험 발행용 — 자동 실행에는 쓰지 않는다(설정이 정본이어야 한다)
    ap.add_argument("--run-id", default=None, help="이 run_id 장면 하나만 발행(시험용)")
    ap.add_argument("--ignore-review", action="store_true",
                    help="검수 게이트 무시(시험·비상용) — 자동 실행에는 절대 쓰지 않는다")
    ap.add_argument("--privacy", default=None, choices=["private", "unlisted"],
                    help="예약 공개 대신 이 상태로만 올린다(시험용). 설정의 publish_privacy 를 덮는다")
    a = ap.parse_args()

    def log(m):
        print(m, flush=True)

    # 🛑 담당 채널은 생성측과 **같은 해석기**로 구한다. 발행측이 따로 목록을 들면 생성과 어긋나
    # 남의 채널에 올라간다(정본은 config/assignments.json).
    policy, chans, mode = sl.resolve_run_config(sl.load_config(), log, machine=a.machine)
    records = registry.load_channels()
    chans = [publish_config(c, policy, records) for c in chans]
    if a.privacy:
        for c in chans:
            c["publish_privacy"] = a.privacy
    if a.channel:
        chans = [c for c in chans if c["channel"] == a.channel]
        if not chans:
            sys.exit(f"채널 '{a.channel}' 이 이 머신 담당이 아닙니다")
    cfg = dict(policy)
    cfg["channels"] = chans
    gen_state = load_json(GEN_STATE_PATH, {})
    pub_state = load_json(PUB_STATE_PATH, {})
    pub_state.setdefault("scenes", {})

    log(f"=== scene_publish_loop {datetime.now().isoformat(timespec='seconds')} dry_run={a.dry_run} ===")

    if not a.skip_publish:
        # 🛑 DB 정합이 안 되면 발행하지 않는다 — 이미 올라간 것을 가려낼 수 없는 상태에서
        # 업로드하면 같은 영상이 두 번 올라가고, 되돌리는 건 사람 손이다.
        if not seed_published_from_db(gen_state, pub_state, log):
            sys.exit("⛔ PIPELINE_DB_URL 미설정/연결 실패 — 이미 발행된 장면을 가려낼 수 없어 "
                     "발행을 중단한다(중복 업로드 위험). --skip-publish 는 현황 보고만 한다")
        todo = pending_scenes(gen_state, pub_state)
        if a.run_id:
            todo = [t for t in todo if t[3].get("run_id") == a.run_id]
            if not todo:
                sys.exit(f"run_id '{a.run_id}' 가 미발행 목록에 없습니다(이미 발행됐거나 오타)")
        log(f"[발행] 미발행 장면 {len(todo)}건" + (f" (--run-id {a.run_id} 로 한정)" if a.run_id else ""))
        # ── 검수 게이트 (4단계, 2026-08-05): 합격작만 발행한다 ──
        # 결정을 못 읽으면 발행 자체를 멈춘다(미검수분이 올라가는 사고 방지 — 안전 방향 실패).
        if a.ignore_review:
            decisions = None
            log("⚠ --ignore-review: 검수 게이트 생략(시험용)")
        else:
            try:
                decisions = fetch_review_decisions([t[3]["run_id"] for t in todo])
            except Exception as e:  # noqa: BLE001
                sys.exit(f"⛔ 검수 결정 조회 실패({type(e).__name__}: {e}) — 미검수분 발행 위험으로 중단")
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
            take = sc.get("take") or "shorts_1"
            skey = state_key(rid, take)
            rec = pub_state["scenes"].setdefault(skey, {"channel": ch_name, "slot": slot,
                                                        "episode": ep_num, "take": take})
            rec.setdefault("channel", ch_name)
            if decisions is not None:
                gate = review_gate(decisions.get((rid, take)))
                if gate == REVIEW_GATE_HOLD:
                    log(f"  ⏸ [{ch_name} EP{ep_num} run={rid[:12]}] 검수 대기(결정 없음) → 발행 보류")
                    continue
                if gate == REVIEW_GATE_REJECT:
                    d = decisions[(rid, take)]
                    if not rec.get("rejected_at"):
                        # rejected_at 은 scene_loop classify_scenes 가 최우선으로 읽는 필드 —
                        # 다음 생성 실행에서 유튜브 조회 없이 즉시 슬롯이 해제된다(8/4 훅 재사용).
                        # reject_type 은 scene_loop.dedup_spans 가 읽는다 — production 이면
                        # 그 구간을 중복 회피에서 빼 같은 장면 재시도를 허용한다(0009).
                        rec.update(stage="review_rejected", rejected_at=d[1],
                                   review_note=d[2] or None,
                                   reject_type=(d[3] or "scene"))
                        if not a.dry_run:
                            save_pub_state(pub_state)
                        log(f"  ❌ [{ch_name} EP{ep_num} run={rid[:12]}] 검수 반려 → 발행 안 함"
                            f"·슬롯 해제{' — ' + d[2] if d[2] else ''}")
                    continue
                if rec.get("stage") == "review_rejected":
                    # 반려였다가 재검수로 합격된 경우 — 반려 표식을 걷어야 발행이 진행된다
                    rec.pop("rejected_at", None)
                    rec["stage"] = None
                    log(f"  ↻ [{ch_name} EP{ep_num} run={rid[:12]}] 반려→합격 재결정 감지, 발행 재개")
            publish_scene(ch_name, ep_num, sc, rec, log, a.dry_run, ch_cfg, pub_state)
            if not a.dry_run:
                save_pub_state(pub_state)

    report_schedule(cfg, pub_state, log)
    if not a.dry_run:
        save_pub_state(pub_state)
    log("=== scene_publish_loop 종료 ===")


if __name__ == "__main__":
    main()
