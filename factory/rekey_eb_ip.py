#!/usr/bin/env python
"""§3-1④ eb_ip 재키잉 + eb_shorts_features.ip_key 백필 (1회성 데이터 수리).

수리 내용:
  1) eb_ip: 't:'+정규화제목 행이 코드-키 행과 같은 원작이면 → 코드-키 행으로 병합(t: 행 삭제)
  2) eb_shorts_features.ip_key 백필:
     (i)  identification_code 보유 행 → ip_key = 코드
     (ii) 제목만 연결된 행 → eb_ip 코드-키 행과 정규화 제목 매칭 → 코드 상속
          (코드-키 행이 없으면 t: 행이라도 매칭 — 최소한 모집단은 안 갈라지게)
     (iii) 둘 다 실패(비라이선스·자체제작) → 미해결로 리포트. description 기반 재해석은
          backfill_clusters.py 재실행(이제 ip_key 도 씀)으로.
  3) 끝나면 안내: python run_factory.py --score-only --score-mode mutual  (재채점 1회)

기본 dry-run(쓰기 0). 실제 반영은 --apply.
선행: 마이그레이션 docs/migrations/0002_eb_ip_key_origin.sql (ip_key/origin 컬럼) 적용.
"""
import argparse
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from cluster import _norm_title
from config import load_settings
from db import Pipeline


# ─────────────────────────── 순수 (단위테스트) ───────────────────────────
def _norm_code_index(ip_rows):
    """정규화 제목 → 코드-키. 충돌(같은 정규화 제목에 서로 다른 코드 — 시즌·리메이크)은
       ambiguous 로 빼고 매핑에서 제외(임의 병합·오귀속 방지, cluster.IPRegistry 와 동일 규칙)."""
    by_norm, ambiguous = {}, set()
    for r in ip_rows:
        if not r.get("identification_code"):
            continue
        nt = _norm_title(r.get("title"))
        if not nt or nt in ambiguous:
            continue
        prev = by_norm.get(nt)
        if prev is not None and prev != r["ip_key"]:
            del by_norm[nt]
            ambiguous.add(nt)
            continue
        by_norm[nt] = r["ip_key"]
    return by_norm, ambiguous


def plan_ip_merges(ip_rows) -> dict:
    """eb_ip 행들 → {t:키: 코드키} 병합 계획. 같은 정규화 제목의 코드-키 행이 **유일**할 때만."""
    by_norm_code, _amb = _norm_code_index(ip_rows)
    merges = {}
    for r in ip_rows:
        k = r.get("ip_key") or ""
        if k.startswith("t:"):
            code = by_norm_code.get(k[2:]) or by_norm_code.get(_norm_title(r.get("title")))
            if code:
                merges[k] = code
    return merges


def plan_sf_backfill(sf_rows, ip_rows, merges=None):
    """eb_shorts_features 행들 → (updates, unresolved_shorts_ids).
       updates = [{"shorts_id", "ip_key"}].
       - ip_key 없음: 코드 → 제목 매칭(코드-키 우선, 없으면 t: 폴백) 순으로 채움
       - ip_key 가 병합 대상 t: 키: 코드-키로 재키잉 — 안 하면 t: eb_ip 행 DELETE 후
         dangling 참조 + 모집단 분열 유지(리뷰 확정 결함)"""
    merges = merges or {}
    by_norm_code, _amb = _norm_code_index(ip_rows)
    by_norm_any = {}
    for r in ip_rows:
        nt = _norm_title(r.get("title"))
        if nt:
            by_norm_any.setdefault(nt, r["ip_key"])
    updates, unresolved = [], []
    for r in sf_rows:
        cur = r.get("ip_key")
        if cur:
            if cur in merges:                     # 병합되는 t: 키 보유 행 → 코드-키로 치환
                updates.append({"shorts_id": r["shorts_id"], "ip_key": merges[cur]})
            continue
        code = r.get("identification_code")
        if code:
            updates.append({"shorts_id": r["shorts_id"], "ip_key": code})
            continue
        nt = _norm_title(r.get("licensed_video_title"))
        hit = (by_norm_code.get(nt) or by_norm_any.get(nt)) if nt else None
        if hit:
            updates.append({"shorts_id": r["shorts_id"], "ip_key": merges.get(hit, hit)})
        else:
            unresolved.append(r["shorts_id"])
    return updates, unresolved


# ─────────────────────────── I/O ───────────────────────────
def main():
    ap = argparse.ArgumentParser(description="§3-1④ eb_ip 재키잉 + ip_key 백필")
    ap.add_argument("--apply", action="store_true", help="실제 반영(기본 dry-run)")
    args = ap.parse_args()
    cfg = load_settings()
    pipe = Pipeline(cfg.get("PIPELINE_URL", ""), cfg.get("PIPELINE_SERVICE_KEY", ""))

    ip_rows = pipe.select("eb_ip", {"select": "ip_key,identification_code,title,cluster_id"})
    sf_rows = pipe.select("eb_shorts_features", {
        "select": "shorts_id,identification_code,licensed_video_title,ip_key"})
    print(f"[rekey] eb_ip {len(ip_rows)}행 · eb_shorts_features {len(sf_rows)}행")

    merges = plan_ip_merges(ip_rows)
    updates, unresolved = plan_sf_backfill(sf_rows, ip_rows, merges=merges)

    print(f"[rekey] eb_ip 병합(t:→코드) {len(merges)}건")
    for t, c in sorted(merges.items())[:10]:
        print(f"    {t} → {c}")
    print(f"[rekey] ip_key 백필/재키잉 {len(updates)}건 · 미해결 {len(unresolved)}건"
          f" (미해결은 backfill_clusters.py 재실행 대상)")

    if not args.apply:
        print("[dry-run] 쓰기 없음 — 반영은 --apply")
        return

    if updates:
        pipe.upsert("eb_shorts_features",
                    [{"shorts_id": u["shorts_id"], "ip_key": u["ip_key"]} for u in updates],
                    on_conflict="shorts_id")
    # DELETE 안전장치: 업데이트 반영 후에도 그 t: 키를 참조하는 sf 행이 남아 있으면 삭제 보류
    updated_keys = {u["shorts_id"]: u["ip_key"] for u in updates}
    still_ref = {}
    for r in sf_rows:
        k = updated_keys.get(r["shorts_id"], r.get("ip_key"))
        if k and k.startswith("t:"):
            still_ref.setdefault(k, 0)
            still_ref[k] += 1
    import urllib.parse
    deleted = kept = 0
    for t_key in merges:                      # 병합된 t: 행 제거(코드-키 행이 정본)
        if still_ref.get(t_key):
            print(f"  ⚠ {t_key}: 여전히 {still_ref[t_key]}개 sf 행이 참조 — 삭제 보류")
            kept += 1
            continue
        pipe._req("DELETE", f"/rest/v1/eb_ip?ip_key=eq.{urllib.parse.quote(t_key)}",
                  headers={"Prefer": "return=minimal"})
        deleted += 1
    print(f"[apply] 완료 (t: 행 삭제 {deleted} · 보류 {kept}) — 다음: "
          f"python run_factory.py --score-only --score-mode mutual (모집단 통일 후 전체 재채점)")


if __name__ == "__main__":
    main()
