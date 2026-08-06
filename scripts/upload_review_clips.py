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
import urllib.parse
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

def iter_state_scenes(state, my_channels, known_channels=None):
    """상태파일 → [(channel, episode, scene, in_assignment)] 평탄화.

    ⚠️ 상태 최상위 키는 **슬롯**이다 — 다작품 채널은 '재미쇼츠·유미의 세포들 시즌3' 처럼
    채널명과 다르다. 채널은 슬롯의 'channel' 필드가 정본이고, 없으면(구 상태) '·' 앞부분을
    등록 채널명(known_channels)과 대조해 복원한다 — scene_publish_loop.pending_scenes 와
    같은 규칙. 복원 없이는 배정 채널의 장면이 '배정 밖'으로 영구 제외된다(2026-08-05 맥5 실측:
    재미쇼츠 장면 3건이 검수함에 안 올라감)."""
    out = []
    mine = set(my_channels)
    known = set(known_channels) if known_channels is not None else None
    for slot, cdata in (state.get("channels") or {}).items():
        ch = cdata.get("channel") or slot
        if known is not None and ch not in known and "·" in ch:
            head = ch.split("·")[0].strip()
            if head in known:
                ch = head
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


def object_path(machine_id, clip_id):
    """버킷 내 객체 경로 — **clip_id(uuid)** 로 명명한다.

    run_id 를 못 쓰는 이유: Supabase Storage 는 오브젝트 키에 한글을 거부한다
    (InvalidKey, 2026-08-05 맥1 실측 — percent-encode 해도 서버가 키 문자 자체를 검사한다).
    run_id 는 전 작품이 한글이므로 ASCII 확정인 clip_id 를 쓴다. 업로드 시점엔 ①(ingest)이
    끝나 clip_id 가 항상 있고, 대시보드·정리는 clips.storage_path 를 읽으므로 키 형식 무관.
    '/' 치환은 경로 붕괴 방지(uuid 엔 없음 — 방어).
    """
    return f"{machine_id}/{str(clip_id).replace('/', '_')}.mp4"


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


def needs_judge(clip_row, clip_id_judged, clip_id_decided):
    """검수 전 judge 선실행 대상인가 (2026-08-05 — judge 를 검수 앞으로).

    목적: 검수함에 뜨는 시점에 judge 점수·사유가 함께 보이게 한다. **표시용이다** —
    합격/반려는 100% 사람이고, judge 는 발행 직전 안전게이트(환각) 역할만 유지한다.

    제외 3부류:
      - 이미 judge 있음 — 재실행은 Gemini 비용 중복 (발행 픽업도 이 판정으로 생략한다)
      - 이미 사람이 결정함 — 검수가 끝났으니 선실행의 목적이 사라짐 (비용 절약)
      - 이미 발행됨 — 구 흐름에서 발행 직전 judge 를 거쳤다
    """
    if clip_row is None:
        return False
    clip_id, video_id, _ = clip_row
    return (not video_id and clip_id not in clip_id_judged
            and clip_id not in clip_id_decided)


def episode_pairs(scenes, rows):
    """[(clip_id, 회차 int)] — DB 에 클립이 있는 배정 내 장면만. 회차가 숫자가 아니면 건너뜀.

    회차(원작 에피소드 번호)는 상태파일 episodes 키에만 있다 — clips.episode 는 쇼츠
    라벨(shorts_1)이라 회차가 아니다. 이 짝을 clips.source_episode 로 스탬프한다(0010)."""
    out = []
    for _ch, ep, sc, mine in scenes:
        row = rows.get(sc.get("run_id")) if mine else None
        if not row:
            continue
        try:
            out.append((row[0], int(ep)))
        except (TypeError, ValueError):
            pass
    return out


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


def stamp_source_episode(conn, pairs):
    """clips.source_episode 스탬프 — 비어 있는 행만(멱등). 회차는 run 에 대해 불변이다."""
    if not pairs:
        return
    with conn.cursor() as cur:
        for clip_id, ep in pairs:
            cur.execute(
                "UPDATE public.clips SET source_episode = %s WHERE id = %s AND source_episode IS NULL",
                (ep, clip_id))
    conn.commit()


def judged_clip_ids(conn, clip_ids):
    """judge_runs 가 있는 clip_id 집합."""
    if not clip_ids:
        return set()
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT clip_id FROM public.judge_runs WHERE clip_id = ANY(%s)",
                    (list(clip_ids),))
        return {r[0] for r in cur.fetchall()}


def decided_clip_ids(conn, clip_ids):
    """review_decisions 가 있는 clip_id 집합 (합격·반려 무관 — 검수가 끝난 것)."""
    if not clip_ids:
        return set()
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT clip_id FROM public.review_decisions WHERE clip_id = ANY(%s)",
                    (list(clip_ids),))
        return {r[0] for r in cur.fetchall()}


def run_judge(clip_id, video):
    """run_judge.py 재사용 — judge 프롬프트·기록 로직을 두 벌 만들지 않는다."""
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "run_judge.py"),
         "--clip-id", str(clip_id), "--video", str(video)],
        capture_output=True, text=True, timeout=1800)
    return r.returncode == 0, (r.stdout + r.stderr)[-300:]


def storage_request(url, key, method, path, data=None, content_type=None):
    # run_id 가 한글이라 경로를 percent-encode 해야 한다 — urllib 은 non-ASCII 요청라인을
    # UnicodeEncodeError 로 거부하고, 그 예외가 스캔 전체를 죽인다(2026-08-05 맥1 실측).
    quoted = urllib.parse.quote(path, safe="/")
    req = urllib.request.Request(
        f"{url}/storage/v1/object/{quoted}", data=data, method=method,
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
    ap.add_argument("--no-judge", action="store_true",
                    help="judge 선실행 생략(빠른 업로드 전용) — 자동 실행에는 쓰지 않는다")
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
    scenes = iter_state_scenes(state, my_channels,
                               known_channels=channel_registry.channel_names())

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
                opath = f"{BUCKET}/{object_path(machine_id, row[0])}"
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
        # ── 회차 스탬프 (2026-08-05, 0010): 회차는 상태파일에만 있다 — DB 일급 컬럼으로 ──
        rows2 = clip_rows(conn, [sc["run_id"] for _, _, sc, mine in scenes if mine])
        if not args.dry_run:
            stamp_source_episode(conn, episode_pairs(scenes, rows2))
        # ── judge 선실행 (2026-08-05): 검수함에 점수·사유가 함께 보이게 ──
        # 업로드 뒤에 도는 이유: 사람이 볼 수 있는 상태(사본 존재)를 먼저 만든다 — judge 는
        # 편당 수 분이라, 먼저 돌리면 그동안 검수함이 빈다. 실패는 다음 실행이 재시도.
        n["judge"] = 0
        if not args.no_judge:
            all_ids = [r[0] for r in rows2.values()]
            judged = judged_clip_ids(conn, all_ids)
            decided = decided_clip_ids(conn, all_ids)
            for ch, ep, sc, mine in scenes:
                if not mine or not within_days(sc.get("accepted_at"), args.since_days):
                    continue
                row = rows2.get(sc["run_id"])
                if not needs_judge(row, judged, decided):
                    continue
                job_dir = resolve_job_dir(sc.get("job_dir", ""), ai_video_root)
                mp4 = job_dir / "shorts.mp4"
                if not mp4.exists():
                    continue
                if args.dry_run:
                    print(f"[review-upload] (dry) judge {ch} EP{ep} {sc['run_id']}")
                    n["judge"] += 1
                    continue
                ok, tail = run_judge(row[0], mp4)
                if ok:
                    print(f"[review-upload] ⚖ judge {ch} EP{ep} {sc['run_id']}: {tail.strip().splitlines()[-1] if tail.strip() else 'ok'}")
                    n["judge"] += 1
                else:
                    print(f"[review-upload] ✗ judge 실패 {sc['run_id']}: {tail}")
        print(f"[review-upload] 완료 — 업로드 {n['upload']} (신규적재 {n['ingest']}) · "
              f"judge {n['judge']} · 정리 {n['cleanup']} · skip {n['skip']} · 경고 {n['warn']}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
