#!/usr/bin/env python3
"""검수 사본 업로더 — 렌더 확정 장면을 Storage(review-clips)와 DB 에 올린다.

scene_loop_run.sh 가 매 실행 끝(종료 하트비트 직전)에 1회 호출. 수동 실행도 안전(멱등).
설계: docs/DASHBOARD_REVIEW_STORAGE_DESIGN.md §3 (스캔 방식 — 실패는 다음 실행이 재시도,
기존 백로그도 첫 실행 때 자동 흡수).

장면별 동작 (모두 멱등):
  ① DB 에 클립 없음         → ingest_aivideo_run.py 로 적재 후 ②
  ② 미발행 · 사본 없음       → shorts.mp4 를 review-clips/<machine>/<run_id>.mp4 로 업로드
                               + clips.storage_path 기록
  ③ 미발행 · 사본 있음       → skip (검수 대기 중)
  ④ 발행 확인 · 사본 있음    → **사본 자동 정리**(2026-08-05 운영자 승인 — 파생 캐시라 정보
                               손실 없음. 자기 머신 프리픽스만) + storage_path NULL
  ⑤ 발행 확인 · 사본 없음    → skip

원칙 (하트비트와 동일):
  - 생성을 절대 막지 않는다 — 모든 예외를 삼키고 exit 0.
  - machine_id 역산 실패 시 추측하지 않고 종료(경로 프리픽스를 못 만든다).
  - 배정 밖 채널(재배정 잔재·정본에 없는 이름)은 올리지 않고 경고만.

자격증명: PIPELINE_URL·PIPELINE_SERVICE_KEY (Storage) + PIPELINE_DB_URL (DB) — **루트
.env 가 정본**(2026-08-05 운영자 결정: factory 는 전 맥에서 돌리는 게 아니라 factory/.env
를 전 맥에 강제하지 않는다). factory/.env 는 있으면 빠진 키만 채우는 폴백. 없으면 로그
남기고 skip.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import channel_registry  # noqa: E402
import envload  # noqa: E402

REPO_ROOT = envload.REPO_ROOT
STATE_PATH = REPO_ROOT / "results" / "scene_loop_state.json"
BUCKET = "review-clips"
SHORT_LABEL = "shorts_1"  # scene_loop 은 실행당 1쇼츠 — ingest 멱등 키와 일치해야 한다
HTTP_TIMEOUT = 120  # 업로드 ~30MB — 넉넉히


# ── 순수 로직 (테스트 대상) ──────────────────────────────────

def iter_state_scenes(state, my_channels):
    """상태파일 → [(channel, episode, scene, in_assignment)] 평탄화."""
    out = []
    mine = set(my_channels)
    for ch, cdata in (state.get("channels") or {}).items():
        for ep, edata in (cdata.get("episodes") or {}).items():
            for sc in edata.get("scenes") or []:
                if sc.get("run_id"):
                    out.append((ch, ep, sc, ch in mine))
    return out


def within_days(accepted_at, since_days, today=None):
    """accepted_at(ISO)이 최근 since_days 일 이내인가. since_days None=무제한, 파싱 실패=포함."""
    if since_days is None:
        return True
    try:
        d = dt.date.fromisoformat(str(accepted_at)[:10])
    except ValueError:
        return True
    return ((today or dt.date.today()) - d).days < since_days


def object_path(machine_id, run_id):
    """버킷 내 객체 경로. run_id 의 '/' 는 경로 붕괴 방지로 치환(실측상 없음 — 방어)."""
    return f"{machine_id}/{run_id.replace('/', '_')}.mp4"


def decide(clip_row):
    """DB 행 → 동작. clip_row=None 또는 (video_external_id, storage_path)."""
    if clip_row is None:
        return "ingest_upload"
    video_id, storage_path = clip_row
    if video_id and storage_path:
        return "cleanup"
    if video_id:
        return "skip_published"
    if storage_path:
        return "skip_uploaded"
    return "upload"


def own_object(storage_path, machine_id):
    """정리 가드 — 자기 머신 프리픽스의 사본만 지운다."""
    return bool(storage_path) and storage_path.startswith(f"{BUCKET}/{machine_id}/")


def resolve_job_dir(job_dir, ai_video_root):
    p = pathlib.Path(job_dir)
    return p if p.is_absolute() else pathlib.Path(ai_video_root) / p


# ── I/O ──────────────────────────────────────────────────────

def _db():
    try:
        import psycopg as pg  # v3 (brain venv 정본)
    except ModuleNotFoundError:
        import psycopg2 as pg
    import os
    dsn = os.environ.get("PIPELINE_DB_URL", "")
    if not dsn:
        return None
    return pg.connect(dsn, connect_timeout=10)


def clip_rows(conn, run_ids):
    """{run_id: (clip_id, video_external_id, storage_path)} — auto_edit + short_label 멱등 키."""
    if not run_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """SELECT m.ai_video_run_id, c.id, c.video_external_id, c.storage_path
               FROM public.clip_metadata m JOIN public.clips c ON c.id = m.clip_id
               WHERE m.ai_video_run_id = ANY(%s) AND c.source = 'auto_edit'
                 AND c.episode = %s""",
            (list(run_ids), SHORT_LABEL))
        return {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}


def set_storage_path(conn, clip_id, value):
    with conn.cursor() as cur:
        cur.execute("UPDATE public.clips SET storage_path = %s WHERE id = %s", (value, clip_id))
    conn.commit()


def storage_request(url, key, method, path, data=None, content_type=None):
    req = urllib.request.Request(
        f"{url}/storage/v1/object/{path}", data=data, method=method,
        headers={"authorization": f"Bearer {key}", "apikey": key,
                 **({"content-type": content_type} if content_type else {}),
                 **({"x-upsert": "true"} if method == "POST" else {})})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return r.status


def run_ingest(job_dir, channel):
    """ingest_aivideo_run.py 재사용 — 게이트·멱등 로직을 두 벌 만들지 않는다."""
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "ingest_aivideo_run.py"),
         "--run-dir", str(job_dir), "--short-label", SHORT_LABEL, "--channel", channel],
        capture_output=True, text=True, timeout=300)
    return r.returncode == 0, (r.stdout + r.stderr)[-400:]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--since-days", type=int, default=None,
                    help="accepted_at 이 최근 N일 이내인 장면만 (기본: 전체 — 백로그 포함)")
    ap.add_argument("--dry-run", action="store_true", help="무엇을 할지만 출력, 쓰기 없음")
    args = ap.parse_args(argv)
    try:
        return _run(args)
    except Exception as e:  # 어떤 경우에도 생성/러너를 막지 않는다
        print(f"[review-upload] 내부 오류 무시: {type(e).__name__}: {e}")
        return 0


def _run(args):
    import os
    envload.load_env()
    envload.load_env(REPO_ROOT / "factory" / ".env")
    url = (os.environ.get("PIPELINE_URL") or "").rstrip("/")
    key = os.environ.get("PIPELINE_SERVICE_KEY") or ""
    if not url or not key:
        print("[review-upload] PIPELINE_URL/PIPELINE_SERVICE_KEY 없음(루트 .env 에 추가) → skip")
        return 0
    try:
        machine_id = channel_registry.detect_machine_id()
    except Exception as e:
        print(f"[review-upload] machine_id 역산 실패 → skip (추측하지 않음): {e}")
        return 0
    my_channels = channel_registry.machine_channels(machine_id)
    ai_video_root = os.environ.get("AI_VIDEO_ROOT", str(pathlib.Path.home() / "ves" / "ai-video"))

    if not STATE_PATH.exists():
        print("[review-upload] 상태파일 없음 → skip")
        return 0
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    scenes = iter_state_scenes(state, my_channels)

    conn = _db()
    if conn is None:
        print("[review-upload] PIPELINE_DB_URL 없음 → skip")
        return 0
    try:
        run_ids = [sc["run_id"] for _, _, sc, mine in scenes if mine]
        rows = clip_rows(conn, run_ids)
        n = {"upload": 0, "ingest": 0, "cleanup": 0, "skip": 0, "warn": 0}
        for ch, ep, sc, mine in scenes:
            rid = sc["run_id"]
            if not mine:
                print(f"[review-upload] ⚠ 배정 밖 채널 '{ch}' (run={rid}) — 올리지 않음")
                n["warn"] += 1
                continue
            if not within_days(sc.get("accepted_at"), args.since_days):
                continue
            row = rows.get(rid)
            action = decide((row[1], row[2]) if row else None)

            if action == "ingest_upload":
                job_dir = resolve_job_dir(sc.get("job_dir", ""), ai_video_root)
                if args.dry_run:
                    print(f"[review-upload] (dry) ingest+upload {ch} EP{ep} {rid}")
                    n["ingest"] += 1
                    continue
                ok, tail = run_ingest(job_dir, ch)
                if not ok:
                    print(f"[review-upload] ✗ ingest 실패 {rid}: {tail}")
                    continue
                rows.update(clip_rows(conn, [rid]))
                row = rows.get(rid)
                if row is None:
                    print(f"[review-upload] ✗ ingest 후에도 클립 조회 실패 {rid}")
                    continue
                n["ingest"] += 1
                action = decide((row[1], row[2]))

            if action == "upload":
                job_dir = resolve_job_dir(sc.get("job_dir", ""), ai_video_root)
                mp4 = job_dir / "shorts.mp4"
                if not mp4.exists():
                    cands = sorted(job_dir.glob("*.mp4"))
                    if not cands:
                        print(f"[review-upload] ✗ mp4 없음 {rid} ({job_dir})")
                        continue
                    mp4 = cands[0]
                opath = f"{BUCKET}/{object_path(machine_id, rid)}"
                if args.dry_run:
                    print(f"[review-upload] (dry) upload {ch} EP{ep} {rid} ← {mp4.name}")
                    n["upload"] += 1
                    continue
                storage_request(url, key, "POST", opath, data=mp4.read_bytes(),
                                content_type="video/mp4")
                set_storage_path(conn, row[0], opath)
                print(f"[review-upload] ✓ 업로드 {ch} EP{ep} {rid} → {opath}")
                n["upload"] += 1

            elif action == "cleanup":
                spath = row[2]
                if not own_object(spath, machine_id):
                    continue  # 다른 머신 사본은 그 머신이 정리한다
                if args.dry_run:
                    print(f"[review-upload] (dry) cleanup {rid} ({spath})")
                    n["cleanup"] += 1
                    continue
                try:
                    storage_request(url, key, "DELETE", spath)
                except urllib.error.HTTPError as e:
                    if e.code != 404:  # 이미 없으면 그대로 진행
                        raise
                set_storage_path(conn, row[0], None)
                print(f"[review-upload] 🗑 발행 확인 → 검수 사본 정리 {rid}")
                n["cleanup"] += 1

            else:
                n["skip"] += 1
        print(f"[review-upload] 완료 — 업로드 {n['upload']} (신규적재 {n['ingest']}) · "
              f"정리 {n['cleanup']} · skip {n['skip']} · 경고 {n['warn']}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
