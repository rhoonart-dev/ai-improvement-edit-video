#!/usr/bin/env python3
"""머신 하트비트 송신 — scene_loop 실행 결과 요약 1행을 fdidiqd machine_heartbeats 에 upsert.

scene_loop_run.sh 가 두 번 부른다:
  --phase start                  락 획득 직후 (status='running' INSERT)
  --phase end --rc N             scene_loop 종료 후 (같은 행 UPDATE — 채널 결과·로그·스냅샷)
  --phase end --status blocked   배정 검증 게이트(exit 2)로 생성 없이 종료했을 때

설계 원칙 (docs/migrations/0007_machine_heartbeat.sql 머리 주석과 짝):
  1. **생성을 절대 막지 않는다** — 모든 예외를 삼키고 exit 0. DB 실패는 스풀로 미룬다.
  2. **추측 금지** — machine_id 역산 실패 시 NULL, host 원시값만 남긴다(0006 철학).
  3. **멱등** — (host, run_started_at) upsert. 스풀 재송신이 중복 행을 못 만든다.
  4. 크기 상한은 여기서 자른다 — log_segment 32KB · fail tail 16KB · 스냅샷 64KB.

검수함 데이터 흐름(2026-08-04 A안 확정): 렌더만 되고 미업로드인 장면(③)은 DB에 없다 —
state_snapshot(scene_loop_state.json, 진짜 회차 번호 포함) − publish_snapshot 차집합으로
대시보드가 계산한다. 인제스트 시점을 앞당기지 않는 이유: 발행 전 사람 검수가 공정에 있음.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import envload  # noqa: E402

REPO_ROOT = envload.REPO_ROOT
LOG_PATH = REPO_ROOT / "results" / "scene_loop.log"
STATE_PATH = REPO_ROOT / "results" / "scene_loop_state.json"
PUB_STATE_PATH = REPO_ROOT / "results" / "scene_publish_state.json"
CURRENT_PATH = REPO_ROOT / "results" / "heartbeat_current.json"
SPOOL_DIR = REPO_ROOT / "results" / "heartbeat_spool"

CAP_LOG = 32 * 1024
CAP_TAIL = 16 * 1024
CAP_SNAPSHOT = 64 * 1024
DB_TIMEOUT_SEC = 10
SCHEMA_VERSION = 1

# ── 로그 파싱 (scene_loop.log 실측 포맷 — 2026-08-03 맥1 로그 기준) ──

_RE_START = re.compile(r"^===== (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) scene_loop 시작 =====")
_RE_HEADER = re.compile(  # "[채널 · 작품] EP410: 공개 0/3 (렌더 1, 미공개 1) → ..."
    r"^\[(?P<ch>[^\]·]+) · (?P<work>[^\]]+)\] EP(?P<ep>\d+): 공개 (?P<pub>\d+)/(?P<quota>\d+)"
    r"(?:.*?미공개 (?P<pend>\d+))?")
_RE_PAUSED = re.compile(r"미공개 대기 (?P<pend>\d+)개\(상한 \d+\) → 생성 멈춤")
_RE_TRY = re.compile(r"^\[(?P<ch>[^\]·]+)[^\]]*\]\s+시도 (?P<n>\d)/\d")
_RE_OK = re.compile(r"^\[(?P<ch>[^\]·]+)[^\]]*\]\s+✓ 새 장면 확정.*\(run=(?P<run>[^)]+)\)")
_RE_FAIL = re.compile(r"^\[(?P<ch>[^\]·]+)[^\]]*\]\s+✗ 생성 실패")
_RE_GENLOG = re.compile(r"전문: (?P<path>\S+gen_output\.log)")
_RE_EXC = re.compile(r"^\[(?P<ch>[^\]]+)\] 처리 중 예외: (?P<exc>.+)")
_RE_WAIT_SRC = re.compile(r"^\[(?P<ch>[^\]·]+)[^\]]*\].*소스.*(없|대기)")
_RE_WARN = re.compile(r"^\s*(※|⚠️|⛔)\s*(?P<msg>.+)")
_RE_STAGE = re.compile(r"\[(\d+/\d+)\]\s*(\S[^.\n]*)")

# stderr 꼬리·예외 문구 → 실패 분류 (실측 사례에서 도출 — 미지 패턴은 unknown)
_ERROR_CLASSES = [
    ("llm_json", re.compile(r"JSONDecodeError|_loads_first_json")),
    ("api_quota", re.compile(r"429|RESOURCE_EXHAUSTED|quota", re.I)),
    ("env_config", re.compile(r"FileNotFoundError.*\.venv|No such file or directory: '/Users")),
    ("source_missing", re.compile(r"소스.*없|download|yt-dlp", re.I)),
    ("video_id_mismatch", re.compile(r"video_id")),
    ("code_bug", re.compile(r"TypeError|AttributeError|KeyError|NameError")),
]


def classify_error(text):
    for name, rx in _ERROR_CLASSES:
        if rx.search(text or ""):
            return name
    return "unknown"


def last_run_segment(log_text):
    """마지막 '시작~끝' 구간과 시작 시각(naive KST 문자열)을 돌려준다. 종료 마커가 아직 없으면 끝까지."""
    lines = log_text.splitlines()
    start_i, started_at = None, None
    for i, ln in enumerate(lines):
        m = _RE_START.match(ln)
        if m:
            start_i, started_at = i, m.group(1)
    if start_i is None:
        return "", None
    seg = "\n".join(lines[start_i:])
    return seg[-CAP_LOG:], started_at


def parse_channels(segment):
    """실행 구간 로그 → 채널별 결과 목록. 0007 의 channels jsonb 계약."""
    out = {}  # ch -> dict (한 실행에 채널당 1회)

    def ch_entry(name):
        return out.setdefault(name.strip(), {"channel": name.strip(), "result": None, "tries": 0})

    for ln in segment.splitlines():
        m = _RE_HEADER.match(ln)
        if m:
            e = ch_entry(m.group("ch"))
            e.update(work=m.group("work").strip(), episode=int(m.group("ep")),
                     public=int(m.group("pub")), quota=int(m.group("quota")))
            if m.group("pend") is not None:
                e["pending"] = int(m.group("pend"))
            mp = _RE_PAUSED.search(ln)
            if mp:
                e["pending"] = int(mp.group("pend"))
                e["result"] = "paused_pending"
            continue
        m = _RE_TRY.match(ln)
        if m:
            ch_entry(m.group("ch"))["tries"] = max(ch_entry(m.group("ch"))["tries"], int(m.group("n")))
            continue
        m = _RE_OK.match(ln)
        if m:
            e = ch_entry(m.group("ch"))
            e["result"], e["run_id"] = "generated", m.group("run").strip()
            continue
        m = _RE_FAIL.match(ln)
        if m:
            e = ch_entry(m.group("ch"))
            e["result"] = "failed"
            mg = _RE_GENLOG.search(ln)
            if mg:
                e["gen_log"] = mg.group("path")
            continue
        m = _RE_EXC.match(ln)
        if m:
            e = ch_entry(m.group("ch"))
            e["result"] = "failed"
            e["error_class"] = classify_error(m.group("exc"))
            e["error"] = m.group("exc")[:300]
            continue
        if "stderr꼬리" in ln:
            # 실패 채널(직전 ✗)의 분류를 stderr 로 보강
            for e in reversed(list(out.values())):
                if e.get("result") == "failed" and "error_class" not in e:
                    e["error_class"] = classify_error(ln)
                    break
        m = _RE_WAIT_SRC.match(ln)
        if m and ch_entry(m.group("ch")).get("result") is None:
            ch_entry(m.group("ch"))["result"] = "waiting_source"

    for e in out.values():
        if e["result"] is None:
            e["result"] = "skipped"
        if e.get("result") == "failed" and "error_class" not in e:
            e["error_class"] = "unknown"
    return list(out.values())


def parse_warnings(log_text):
    """마지막 배정 검증 블록의 ※/⚠️/⛔ 라인."""
    lines = log_text.splitlines()
    last = None
    for i, ln in enumerate(lines):
        if "배정 검증" in ln:
            last = i
    if last is None:
        return []
    warns = []
    for ln in lines[last:]:
        if _RE_START.match(ln):
            break
        if re.match(r"^\s*⛔ \d+건", ln):  # 집계 라인("⛔ 0건 · ⚠️ 0건")은 경고가 아니다
            continue
        m = _RE_WARN.match(ln)
        if m:
            warns.append(m.group(0).strip()[:500])
    return warns


def fail_tail(gen_log_path):
    try:
        p = pathlib.Path(gen_log_path)
        if not p.is_absolute():
            p = REPO_ROOT / p
        data = p.read_text(encoding="utf-8", errors="replace")
        tail = "\n".join(data.splitlines()[-80:])
        return tail[-CAP_TAIL:]
    except OSError:
        return None


def gen_stage(tail_text):
    """gen_output.log 꼬리의 마지막 '[N/15] 단계' 마커."""
    last = None
    for m in _RE_STAGE.finditer(tail_text or ""):
        last = f"{m.group(1)} {m.group(2).strip()}"
    return last


# ── 수집 ──

def git_sha(repo):
    try:
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except Exception:
        return None


def load_json_capped(path):
    try:
        raw = pathlib.Path(path).read_text(encoding="utf-8")
        if len(raw) > CAP_SNAPSHOT:
            return {"_truncated": True, "_bytes": len(raw)}
        return json.loads(raw)
    except Exception:
        return None


def detect_machine():
    """(machine_id, host) — 역산 실패 시 (None, host). 절대 추측하지 않는다."""
    host = socket.gethostname()
    try:
        import channel_registry
        mid = channel_registry.detect_machine_id()
        return mid, host
    except Exception:
        return None, host


def kst_to_utc_iso(kst_str):
    """로그 시각(naive, 머신 로컬=KST)을 UTC ISO 로. 파싱 실패 시 None."""
    try:
        d = dt.datetime.strptime(kst_str, "%Y-%m-%d %H:%M:%S")
        return (d - dt.timedelta(hours=9)).replace(tzinfo=dt.timezone.utc).isoformat()
    except Exception:
        return None


def build_row(phase, rc, status, trigger):
    log_text = LOG_PATH.read_text(encoding="utf-8", errors="replace") if LOG_PATH.exists() else ""
    segment, started_kst = last_run_segment(log_text)
    machine_id, host = detect_machine()

    # run_started_at: start 가 기록한 값 > 로그 마커 > 지금 (셋 다 실패해도 행은 남긴다)
    started_iso = None
    if CURRENT_PATH.exists():
        try:
            started_iso = json.loads(CURRENT_PATH.read_text())["run_started_at"]
        except Exception:
            pass
    if phase == "start":
        started_iso = dt.datetime.now(dt.timezone.utc).isoformat()
        CURRENT_PATH.write_text(json.dumps({"run_started_at": started_iso}))
    if not started_iso:
        started_iso = kst_to_utc_iso(started_kst) or dt.datetime.now(dt.timezone.utc).isoformat()

    row = {
        "machine_id": machine_id,
        "host": host,
        "trigger": trigger,
        "run_started_at": started_iso,
        "brain_sha": git_sha(REPO_ROOT),
        "aivideo_sha": git_sha(os.environ.get("AI_VIDEO_ROOT", str(pathlib.Path.home() / "ves" / "ai-video"))),
        "disk_free_gb": round(shutil.disk_usage(str(REPO_ROOT)).free / 1e9, 1),
        "schema_version": SCHEMA_VERSION,
        "status": "running",
    }
    if phase == "end":
        channels = parse_channels(segment)
        tails = {}
        for e in channels:
            if e.get("result") == "failed" and e.get("gen_log"):
                t = fail_tail(e["gen_log"])
                if t:
                    tails[e["channel"]] = {"path": e["gen_log"], "tail": t}
                    st = gen_stage(t)
                    if st:
                        e["stage"] = st
                    if e.get("error_class") in (None, "unknown"):
                        e["error_class"] = classify_error(t)
        row.update({
            "status": status or ("done" if rc == 0 else "failed"),
            "rc": rc,
            "run_finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "channels": channels,
            "warnings": parse_warnings(log_text),
            "log_segment": segment or None,
            "fail_tails": tails or None,
            "state_snapshot": load_json_capped(STATE_PATH),
            "publish_snapshot": load_json_capped(PUB_STATE_PATH),
        })
    return row


# ── 송신 (실패 시 스풀 — 생성을 막지 않는다) ──

_COLS = ["machine_id", "host", "trigger", "status", "rc", "run_started_at", "run_finished_at",
         "brain_sha", "aivideo_sha", "disk_free_gb", "channels", "warnings", "log_segment",
         "fail_tails", "state_snapshot", "publish_snapshot", "schema_version"]
_JSONB = {"channels", "warnings", "fail_tails", "state_snapshot", "publish_snapshot"}


# 문자열 파라미터의 타입 캐스트 — psycopg v3 는 서버측 바인딩이라 text→jsonb/timestamptz
# 암묵 캐스트가 없다(맥1 실측으로 v2 부재 발견 후 v3 전환하며 함께 수정, 2026-08-04).
# v2 에서도 캐스트는 무해하므로 공통으로 붙인다.
_CASTS = {**{c: "::jsonb" for c in _JSONB},
          "run_started_at": "::timestamptz", "run_finished_at": "::timestamptz"}


def _build_upsert(row):
    cols = [c for c in _COLS if c in row and row[c] is not None]
    vals = [json.dumps(row[c], ensure_ascii=False) if c in _JSONB else row[c] for c in cols]
    ph = ", ".join("%s" + _CASTS.get(c, "") for c in cols)
    sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in ("host", "run_started_at"))
    sql = (f"INSERT INTO machine_heartbeats ({', '.join(cols)}) "
           f"VALUES ({ph}) "
           f"ON CONFLICT (host, run_started_at) DO UPDATE SET {sets}")
    return sql, vals


def upsert(row, dsn):
    # brain venv 정본은 psycopg v3(requirements.txt) — v2 만 있는 환경도 동작하게 폴백.
    try:
        import psycopg as pg
    except ModuleNotFoundError:
        import psycopg2 as pg
    sql, vals = _build_upsert(row)
    conn = pg.connect(dsn, connect_timeout=DB_TIMEOUT_SEC)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, vals)
    finally:
        conn.close()


def spool(row):
    SPOOL_DIR.mkdir(parents=True, exist_ok=True)
    # 파일명은 **스풀한 시각** 기준 — run_started_at+status 로 지으면 같은 실행의 start/end 가
    # 알파벳순(done < running)으로 뒤집혀 재송신 시 최종 상태가 running 으로 되돌아간다.
    name = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".json"
    (SPOOL_DIR / name).write_text(json.dumps(row, ensure_ascii=False))


def flush_spool(dsn):
    if not SPOOL_DIR.exists():
        return
    for p in sorted(SPOOL_DIR.glob("*.json")):
        try:
            upsert(json.loads(p.read_text()), dsn)
            p.unlink()
        except Exception:
            return  # DB 가 여전히 안 되면 다음 실행에서 재시도


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["start", "end"], required=True)
    ap.add_argument("--rc", type=int, default=None)
    ap.add_argument("--status", choices=["blocked"], default=None)
    ap.add_argument("--trigger", choices=["launchd", "scheduled", "manual"], default=None)
    args = ap.parse_args(argv)
    try:
        envload.load_env()
        row = build_row(args.phase, args.rc, args.status, args.trigger)
        dsn = os.environ.get("PIPELINE_DB_URL", "")
        if not dsn:
            spool(row)
            print("[heartbeat] PIPELINE_DB_URL 없음 → 스풀에 보관")
            return 0
        try:
            flush_spool(dsn)
            upsert(row, dsn)
            print(f"[heartbeat] {args.phase} 송신 완료 ({row.get('status')})")
        except Exception as e:  # DB 장애 — 스풀로 미루고 성공 종료
            spool(row)
            print(f"[heartbeat] 송신 실패 → 스풀 보관: {type(e).__name__}: {e}")
        return 0
    except Exception as e:  # 어떤 경우에도 생성을 막지 않는다
        print(f"[heartbeat] 내부 오류 무시: {type(e).__name__}: {e}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
