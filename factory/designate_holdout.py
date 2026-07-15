#!/usr/bin/env python
"""§3-7 홀드아웃 지정 도우미 — 채점된 뱅크에서 골든 홀드아웃 클러스터 후보를 고른다.

INJECTION_HOLDOUT_CLUSTERS(주입 금지 카나리아)는 채점(perf_label) 후에만 의미가 있다:
good·bad 가 각각 충분한 클러스터라야 mode collapse 감시가 성립한다. 미채점 뱅크(현재 상태)로는
후보가 안 나오므로, 이 스크립트가 '지금 지정 가능한지'를 알려주는 게이트 역할을 한다.

선정 규칙(순수, 테스트 대상): good≥min AND bad≥min 인 클러스터 중 **표본이 작은 순**으로 k개
(주입 손실 최소화 — 큰 클러스터를 통째로 홀드아웃하면 그 클러스터 생성이 주입 이득을 영영 못 봄).
자사 채널(origin='ours')·비활성은 카운트에서 제외.

사용: python factory/designate_holdout.py   → 붙여넣을 config 라인 출력(쓰기 없음)
"""
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import load_settings
from db import Pipeline


# ─────────────────────────── 순수 (단위테스트) ───────────────────────────
def pick_holdout_clusters(cluster_stats, k=2, min_each=15):
    """cluster_stats: [{cluster_id, good, bad}]. good·bad 각 min_each 이상인 클러스터 중
       표본(good+bad) 작은 순 k개의 cluster_id 리스트. 자격 미달이면 빈 리스트."""
    qualifying = [c for c in cluster_stats
                  if (c.get("good") or 0) >= min_each and (c.get("bad") or 0) >= min_each]
    qualifying.sort(key=lambda c: (c.get("good") or 0) + (c.get("bad") or 0))
    return [c["cluster_id"] for c in qualifying[:k]]


# ─────────────────────────── I/O ───────────────────────────
OUR = ("재미쇼츠", "스토리순삭")


def main():
    cfg = load_settings()
    pipe = Pipeline(cfg.get("PIPELINE_URL", ""), cfg.get("PIPELINE_SERVICE_KEY", ""))
    rows = pipe.select("eb_shorts_features", {
        "select": "cluster_id,perf_label,channel_name,lifecycle_status"})
    stats = {}
    for r in rows:
        if r.get("lifecycle_status") != "active" or not r.get("cluster_id"):
            continue
        if r.get("channel_name") in OUR:            # 자사 제외
            continue
        c = stats.setdefault(r["cluster_id"], {"cluster_id": r["cluster_id"], "good": 0, "bad": 0})
        if r.get("perf_label") == "good":
            c["good"] += 1
        elif r.get("perf_label") == "bad":
            c["bad"] += 1
    stat_list = sorted(stats.values(), key=lambda c: -(c["good"] + c["bad"]))
    scored = sum(c["good"] + c["bad"] for c in stat_list)
    print(f"[holdout] 채점된(good/bad) 시장 클립 {scored}개 · 클러스터 {len(stat_list)}개")
    for c in stat_list[:8]:
        print(f"  {c['cluster_id']:22s} good={c['good']:4d} bad={c['bad']:4d}")
    picks = pick_holdout_clusters(stat_list)
    if not picks:
        print("⚠ 아직 지정 불가 — good·bad 각 15+ 인 클러스터 없음(뱅크 채점 필요). "
              "run_factory.py --score-only 후 재실행")
        return
    print("\n→ factory/config.py 에 붙여넣기:")
    print(f"INJECTION_HOLDOUT_CLUSTERS = frozenset({picks!r})")


if __name__ == "__main__":
    main()
