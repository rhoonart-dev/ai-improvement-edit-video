#!/usr/bin/env python
"""쇼츠 분석 공장 v0.1 — laeebly → (게이트 → 다운로드 → 아카이브 → 2트랙 추출
→ +7D 성과·통제 → 적재) → 채널 갱신 → 점수 → eb_* (video-improvement-pipeline).

추출 로직은 factory 내부 extract.py(결정론+Gemini VLM). factory는 완전 독립 — 외부 폴더 참조 없음.

사용:
  python run_factory.py --dry-run              # 게이트 후보/신규 수만 확인 (쓰기 0)
  python run_factory.py --limit 5              # 신규 5개 처리 (첫 검증용)
  python run_factory.py --limit 5 --no-vlm     # Gemini 없이 결정론만
  python run_factory.py --limit 0              # 신규 전부 (⚠ 3만+ 규모, VLM 비용 주의)
  python run_factory.py --score-only           # 적재된 전체 재채점만 (laeebly 불필요)
  python run_factory.py --ids abc123 def456    # 특정 content_id만 (디버그)

스테이지(eb_factory_items.stage): gate → download → storage_upload → extract_cheap
  → extract_vlm → classify(원작 IP: 라이선스=메타 / 비라이선스=desc+영상) → ingest
  → (배치) perf_aggregate · channel_refresh · score
  ※ classify가 추출 뒤로 온 이유: 비라이선스 원작 식별에 description·영상이 필요.

최초 1회: python run_factory.py --seed-works   (분류 xlsx → eb_ip 선적재)
"""
import argparse
import sys
import time
import traceback

# Windows 콘솔 cp949 방어 (한글/특수문자 출력)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime, timezone
from pathlib import Path

from config import (CODE_VERSION, DOWNLOADS, PERF_WINDOW_DAYS,
                    SCHEMA_VERSION, VIDEO_BUCKET, VLM_SLEEP_SEC, YTDLP_FORMAT,
                    load_settings)
import extract as xt                          # factory 내부 추출 모듈 (독립)
from cluster import IPRegistry, seed_from_xlsx
from db import Laeebly, Pipeline, clean_row
from scoring import channel_l1, compute_scores


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ═════════ 실행 로그 (eb_factory_runs / eb_factory_items) ═════════
class RunLog:
    COUNTS = ["n_candidates", "n_gate_passed", "n_downloaded", "n_extracted",
              "n_scored", "n_ingested", "n_failed", "n_skipped"]

    def __init__(self, pipe: Pipeline, config: dict, dry: bool):
        self.pipe, self.dry = pipe, dry
        self.stats = {k: 0 for k in self.COUNTS}
        self._buf, self.errors = [], []
        self.run_id = None
        if not dry:
            row = pipe.insert_returning("eb_factory_runs", {
                "trigger": "manual", "config": config, "code_version": CODE_VERSION})
            self.run_id = row["run_id"]

    def bump(self, key, n=1):
        self.stats[key] += n

    def item(self, shorts_id, stage, status, message=None, error=None, started_at=None):
        if error:
            self.errors.append(f"{shorts_id}/{stage}: {str(error)[:120]}")
        if self.dry:
            return
        self._buf.append({"run_id": self.run_id, "shorts_id": shorts_id, "stage": stage,
                          "status": status, "message": message,
                          "error": str(error)[:2000] if error else None,
                          "started_at": started_at, "finished_at": now_iso()})
        if len(self._buf) >= 50:
            self.flush()

    def flush(self):
        if self._buf:
            self.pipe._req("POST", "/rest/v1/eb_factory_items", body=self._buf,
                           headers={"Prefer": "return=minimal"})
            self._buf = []

    def finish(self, status=None):
        self.flush()
        if self.dry:
            return
        st = status or ("success" if self.stats["n_failed"] == 0 else "partial")
        self.pipe.update("eb_factory_runs", {"run_id": self.run_id}, {
            "finished_at": now_iso(), "status": st,
            "error_summary": "\n".join(self.errors[:40]) or None, **self.stats})


# ═════════ 다운로드 (yt-dlp, stderr로 생존상태 판별) ═════════
def download(sid: str):
    """(path|None, lifecycle_status, err) 반환."""
    import subprocess
    DOWNLOADS.mkdir(exist_ok=True)
    for ext in (".mp4", ".webm", ".mkv"):
        p = DOWNLOADS / f"{sid}{ext}"
        if p.exists():
            return p, "active", None
    try:
        r = subprocess.run(
            ["yt-dlp", "--no-warnings", "--no-update", "-q",
             "-f", YTDLP_FORMAT, "--no-playlist", "--socket-timeout", "30", "-R", "3",
             "-o", str(DOWNLOADS / "%(id)s.%(ext)s"),
             f"https://www.youtube.com/watch?v={sid}"],
            capture_output=True, text=True, timeout=300,
            encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return None, "active", "download timeout"
    if r.returncode == 0:
        for ext in (".mp4", ".webm", ".mkv"):
            p = DOWNLOADS / f"{sid}{ext}"
            if p.exists():
                return p, "active", None
        return None, "active", "yt-dlp ok but file missing"
    err = (r.stderr or "").strip()
    low = err.lower()
    if "private video" in low:
        status = "private"
    elif "unavailable" in low or "removed" in low or "terminated" in low:
        status = "deleted"
    else:
        status = "active"                     # 네트워크 등 일시 오류 — 재시도 여지
    return None, status, err[:300]


# eb_shorts_features에 없는 laeebly control 키(작품 레지스트리로만 쓰는 것) — 행에 안 씀
CTL_NON_COLUMN = {"content_id", "canonical_title"}


# ═════════ 영상 1개 처리 ═════════
def process_video(cand, perf, ctl, registry, pipe, log, key, model, args):
    sid = cand["content_id"]
    rec = {
        "shorts_id": sid,
        "video_url": f"https://www.youtube.com/shorts/{sid}",
        "schema_version": SCHEMA_VERSION,
        "perf_window_days": PERF_WINDOW_DAYS,
        "gate_first_snapshot_ok": True,
        "gate_coverage_days": cand.get("gate_coverage_days"),
        "updated_at": now_iso(),
    }
    if perf:
        rec.update({k: v for k, v in perf.items() if k != "content_id"})
    if ctl:
        rec.update({k: v for k, v in ctl.items() if k not in CTL_NON_COLUMN})
    title = rec.get("title_text")

    # 1) download
    t0 = now_iso()
    path, lifecycle, err = download(sid)
    rec["lifecycle_status"] = lifecycle
    if path:
        log.item(sid, "download", "success", started_at=t0)
        log.bump("n_downloaded")
    else:
        log.item(sid, "download", "failed", message=f"lifecycle={lifecycle}",
                 error=err, started_at=t0)
        log.bump("n_failed")

    if path:
        # 2) storage_upload
        t0 = now_iso()
        try:
            opath = f"{sid}{path.suffix}"
            nbytes = pipe.storage_upload(VIDEO_BUCKET, opath, path)
            rec["storage_path"] = f"{VIDEO_BUCKET}/{opath}"
            rec["video_archived_at"] = now_iso()
            log.item(sid, "storage_upload", "success", message=f"{nbytes // 1024}KB",
                     started_at=t0)
        except Exception as e:
            log.item(sid, "storage_upload", "failed", error=e, started_at=t0)
            log.bump("n_failed")

        # 3) extract_cheap (결정론 트랙 — extract.py)
        t0 = now_iso()
        try:
            det = xt.deterministic_extract(path, title=title, key=key, do_embed=args.embed)
            rec.update({k: v for k, v in det.items() if not k.startswith("_")})
            rec["description_text"] = xt.fetch_description(sid)
            rec["ocr_used"] = det.get("subtitle_density") is not None
            rec["asr_used"] = False
            rec["extracted_at"] = now_iso()
            log.item(sid, "extract_cheap", "success", started_at=t0)
        except Exception as e:
            log.item(sid, "extract_cheap", "failed", error=e, started_at=t0)
            log.bump("n_failed")

        # 4) extract_vlm (Gemini — extract.py)
        if key and not args.no_vlm:
            t0 = now_iso()
            try:
                data = xt.vlm_extract(path, key, model)
                issues = xt.validate_buckets(data)
                for b in xt.BUCKETS:
                    if isinstance(data.get(b), dict):
                        rec[b] = data[b]
                rec["overall_confidence"] = data.get("overall_confidence")
                rec["onscreen_title_text"] = data.get("onscreen_title_text")
                rec["vlm_model"] = model
                log.item(sid, "extract_vlm", "success",
                         message=f"WARN{issues}" if issues else None, started_at=t0)
                log.bump("n_extracted")
                time.sleep(VLM_SLEEP_SEC)
            except Exception as e:
                log.item(sid, "extract_vlm", "failed", error=e, started_at=t0)
                log.bump("n_failed")
        else:
            log.item(sid, "extract_vlm", "skipped", message="no-vlm")
            log.bump("n_extracted")           # 결정론까지는 추출됨

    # 5) classify — 원작(IP) 해석. 라이선스=ctl만 / 비라이선스=description+영상.
    #    (다운로드·설명란 추출 뒤라야 비라이선스 식별 가능. 영상 unlink 전에 수행.)
    t0 = now_iso()
    try:
        cluster_id, _tone, is_licensed, has_src = registry.resolve(
            ctl or {}, description=rec.get("description_text"), video_path=path)
        rec["cluster_id"] = cluster_id
        rec["is_laeebly_licensed"] = is_licensed
        rec["has_source_video"] = has_src
        log.item(sid, "classify", "success",
                 message=(f"cluster={cluster_id}" if cluster_id
                          else ("self_made" if has_src is False else "no_cluster")),
                 started_at=t0)
    except Exception as e:
        log.item(sid, "classify", "failed", error=e, started_at=t0)

    if path and not args.keep_videos:
        path.unlink(missing_ok=True)

    # 6) ingest (성과·통제는 항상 — 영상이 죽었어도 레코드는 남긴다)
    t0 = now_iso()
    try:
        if not args.embed:
            rec.pop("title_embedding", None)
        pipe.upsert("eb_shorts_features", [clean_row(rec)], on_conflict="shorts_id")
        log.item(sid, "ingest", "success", started_at=t0)
        log.bump("n_ingested")
        return True
    except Exception as e:
        log.item(sid, "ingest", "failed", error=e, started_at=t0)
        log.bump("n_failed")
        return False


# ═════════ 배치 스테이지 ═════════
def stage_channels(lae, pipe, log):
    t0 = now_iso()
    try:
        rows = pipe.select("eb_shorts_features", {
            "select": "shorts_id,channel_id,channel_name,publish_time,views"})
        chan_ids = sorted({r["channel_id"] for r in rows if r["channel_id"]})
        snaps = lae.channel_snapshots(chan_ids)
        base = lae.channel_baseline_shorts(chan_ids)     # laeebly 완전 채널 이력
        payload = channel_l1(rows, snaps, baseline_rows=base)
        if payload:
            pipe.upsert("eb_channels", [clean_row(r) for r in payload],
                        on_conflict="channel_id")
        log.item("-", "channel_refresh", "success",
                 message=f"{len(payload)} channels", started_at=t0)
        print(f"[channels] {len(payload)}개 채널 L1 갱신 (기저 pool {len(base)}쇼츠)")
    except Exception as e:
        log.item("-", "channel_refresh", "failed", error=e, started_at=t0)
        log.bump("n_failed")
        print(f"[channels] 실패: {e}")


def stage_score(lae, pipe, log, mode="asof"):
    """mode='asof': 신규(미채점)만, 발행 이전 대비 채점 → 라벨 고정(대규모 재채점 회피).
       mode='mutual': 전체 상호 재채점(백로그 최초 / 재베이스라인)."""
    t0 = now_iso()
    try:
        rows = pipe.select("eb_shorts_features", {
            "select": ("shorts_id,channel_id,cluster_id,identification_code,"
                       "licensed_video_title,publish_time,views,kept_watching_rate,"
                       "likes,shares,comments_added,lift_views,performance_score")})
        # lift 분모 pool = laeebly 완전 채널 이력. asof면 신규(미채점) 채널만 당기면 됨.
        if mode == "mutual":
            chan_ids = sorted({r["channel_id"] for r in rows if r["channel_id"]})
        else:
            chan_ids = sorted({r["channel_id"] for r in rows
                               if r["channel_id"] and r.get("performance_score") is None})
        base = lae.channel_baseline_shorts(chan_ids)
        updates = compute_scores(rows, baseline_rows=base, asof=(mode != "mutual"))
        if updates:
            pipe.upsert("eb_shorts_features", [clean_row(u) for u in updates],
                        on_conflict="shorts_id")
        log.item("-", "score", "success",
                 message=f"{mode}: {len(updates)} scored / base {len(base)}", started_at=t0)
        log.bump("n_scored", len(updates))
        print(f"[score/{mode}] {len(updates)}개 채점 (lift 분모 pool {len(base)}쇼츠)")
    except Exception as e:
        log.item("-", "score", "failed", error=e, started_at=t0)
        log.bump("n_failed")
        print(f"[score] 실패: {e}")


# ═════════ main ═════════
def main():
    ap = argparse.ArgumentParser(description="쇼츠 분석 공장 v0.1")
    ap.add_argument("--limit", type=int, default=5,
                    help="이번 실행 신규 처리 수 (0=전부, 기본 5)")
    ap.add_argument("--ids", nargs="*", help="특정 content_id만 처리(디버그)")
    ap.add_argument("--no-vlm", action="store_true", help="Gemini 끄고 결정론만")
    ap.add_argument("--embed", action="store_true", help="제목 임베딩(768d) 추가")
    ap.add_argument("--no-score", action="store_true", help="채점 스테이지 생략")
    ap.add_argument("--no-channels", action="store_true", help="채널 갱신 생략")
    ap.add_argument("--score-only", action="store_true",
                    help="채점만 (lift 분모=laeebly 완전 이력이라 LAEEBLY_DB_URL 필요)")
    ap.add_argument("--score-mode", choices=["asof", "mutual"], default="asof",
                    help="asof=신규만 이전대비(기본) / mutual=전체 상호(백로그·재베이스라인)")
    ap.add_argument("--seed-works", action="store_true",
                    help="분류 xlsx → eb_ip 선적재 후 종료 (최초 1회)")
    ap.add_argument("--keep-videos", action="store_true", help="로컬 mp4 보존(기본 삭제)")
    ap.add_argument("--dry-run", action="store_true", help="게이트 수만 확인, 쓰기 0")
    args = ap.parse_args()

    cfg = load_settings()
    pipe = Pipeline(cfg.get("PIPELINE_URL", ""), cfg.get("PIPELINE_SERVICE_KEY", ""))
    key, model = cfg.get("GEMINI_API_KEY", ""), cfg.get("GEMINI_MODEL")

    if args.seed_works:
        lae = Laeebly(cfg.get("LAEEBLY_DB_URL", ""))
        rep = seed_from_xlsx(pipe, lae)
        print(f"[seed-works] laeebly작품 {rep['laeebly_works']} | "
              f"xlsx매칭 {rep['matched_titles']} → 적재 {rep['seeded']} | "
              f"미매칭 {rep['n_unmatched']}")
        if rep["unmatched_sample"]:
            print("  미매칭 예:", ", ".join(rep["unmatched_sample"]))
        return

    if args.score_only:
        lae = Laeebly(cfg.get("LAEEBLY_DB_URL", ""))   # lift 분모=laeebly 완전 이력이라 필요
        log = RunLog(pipe, {"mode": "score-only", "score_mode": args.score_mode}, dry=False)
        stage_score(lae, pipe, log, mode=args.score_mode)
        log.finish()
        return

    lae = Laeebly(cfg.get("LAEEBLY_DB_URL", ""))
    registry = IPRegistry(pipe, key, model)
    print(f"[registry] eb_ip 캐시 {len(registry.cache)}개 원작 로드")

    # ── gate ──
    print("[gate] laeebly 커버리지 검증(+7D, upload_at 기준) 조회 중…")
    cands = lae.gate_candidates()
    existing = {r["shorts_id"] for r in
                pipe.select("eb_shorts_features", {"select": "shorts_id"})}
    todo = [c for c in cands if c["content_id"] not in existing]
    if args.ids:
        todo = [c for c in cands if c["content_id"] in set(args.ids)]
    n_skip = len(cands) - len(todo)
    if args.limit:
        todo = todo[:args.limit]
    print(f"[gate] 통과 {len(cands)} | 기적재 {n_skip} | 이번 처리 {len(todo)}"
          f" | VLM {'OFF' if (args.no_vlm or not key) else 'ON ' + model}")

    if args.dry_run:
        print("[dry-run] 종료 (쓰기 없음)")
        return

    log = RunLog(pipe, {
        "limit": args.limit, "no_vlm": args.no_vlm or not key, "embed": args.embed,
        "gate": f"upload_at<=publish+1d & >=publish+{PERF_WINDOW_DAYS}d",
    }, dry=False)
    log.bump("n_candidates", len(cands))
    log.bump("n_gate_passed", len(cands))
    log.bump("n_skipped", n_skip)
    for c in todo:
        log.item(c["content_id"], "gate", "success",
                 message=f"coverage={c.get('gate_coverage_days')}d")

    try:
        # ── perf + control (배치) ──
        ids = [c["content_id"] for c in todo]
        perf_map, ctl_map = {}, {}
        if ids:
            print(f"[perf] +7D 창 집계 {len(ids)}개…")
            t0 = now_iso()
            perf_map = lae.perf_window(ids)
            ctl_map = lae.control(ids)
            log.item("-", "perf_aggregate", "success",
                     message=f"{len(perf_map)}/{len(ids)}", started_at=t0)

        # ── 영상 루프 ──
        for i, c in enumerate(todo, 1):
            sid = c["content_id"]
            try:
                ok = process_video(c, perf_map.get(sid), ctl_map.get(sid),
                                   registry, pipe, log, key, model, args)
                print(f"  [{i}/{len(todo)}] {sid} {'적재' if ok else '실패'}")
            except Exception as e:
                traceback.print_exc()
                log.item(sid, "ingest", "failed", error=e)
                log.bump("n_failed")

        # ── 채널 갱신 + 채점 ──
        if not args.no_channels:
            stage_channels(lae, pipe, log)
        if not args.no_score:
            stage_score(lae, pipe, log, mode=args.score_mode)
    except Exception:
        log.finish(status="failed")
        raise
    log.finish()
    print(f"[done] run={log.run_id} | 신규분류 라이선스 {registry.n_classified}"
          f"·비라이선스 {registry.n_unlicensed} | {log.stats}")


if __name__ == "__main__":
    main()
