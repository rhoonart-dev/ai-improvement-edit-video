#!/usr/bin/env python3
"""
M1 moat 게이트 (clean) — 편집/관찰 피처가 컨텍스트(작품·채널·길이) 너머 +14일 성과를 설명하는가?

설계: docs/M1_FEATURE_EXTRACTION.md. **v_training_matrix 우회**(reward/feature_version 다중행 누수 방지) —
clip_features 를 clips/works/channels/clip_performance 와 직접 조인해 클립당 1행 보장.
관찰형(raw_features jsonb) num/bool 포함. nested 5-fold CV Spearman + 순열검정 p.

실행: PIPELINE_DB_URL=... /Users/gimsewon/rhoonart/ai-video/.venv/bin/python \
        scripts/m1_moat_gate.py --feature-version det+obs-v1 --permute 100
의존: pandas scikit-learn scipy psycopg
"""
import argparse
import os

import numpy as np
import pandas as pd
import psycopg
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import KFold, cross_val_predict

EDIT_DET = ["cut_count", "avg_shot_len_sec", "cut_rhythm_var", "hook_timing_sec",
            "transition_count", "silence_ratio", "speech_ratio"]
EDIT_SEM = ["narrative_completeness", "climax_included", "hook_semantic_strength",
            "dialogue_density", "action_density"]
OBS_NUM = ["num_speakers", "face_count_first3s", "topic_shift_count"]
OBS_BOOL = ["conflict_present", "has_punchline", "question_hook"]


def load(conn, fv):
    cols = ", ".join("f." + c for c in EDIT_DET + EDIT_SEM)
    df = pd.read_sql(f"""
        SELECT w.title AS work_title, ch.channel_external_id AS channel_ext, c.duration_sec,
               p.avg_view_pct AS apv, p.views, f.raw_features, {cols}
        FROM clip_features f
        JOIN clips c ON c.id = f.clip_id
        LEFT JOIN works w ON w.id = c.work_id
        LEFT JOIN channels ch ON ch.id = c.channel_id
        JOIN clip_performance p ON p.clip_id = f.clip_id AND p.snapshot_window_days = 14
        WHERE f.feature_version = %s AND c.is_format_short AND p.avg_view_pct IS NOT NULL
    """, conn, params=(fv,))
    obs = df["raw_features"].apply(lambda r: r if isinstance(r, dict) else {})
    for c in OBS_NUM:
        df[c] = pd.to_numeric(obs.apply(lambda d: d.get(c)), errors="coerce")
    for c in OBS_BOOL:
        df[c] = obs.apply(lambda d: 1.0 if d.get(c) is True else (0.0 if d.get(c) is False else np.nan))
    df["work_code"] = df["work_title"].fillna("NA").astype("category").cat.codes
    df["chan_code"] = df["channel_ext"].fillna("NA").astype("category").cat.codes
    return df


def cvr(df, cols, ncat, y, cv):
    X = df[cols].apply(pd.to_numeric, errors="coerce")
    X = X.fillna(X.median()).fillna(0)
    m = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05,
                                      categorical_features=list(range(ncat)) if ncat else None)
    return float(spearmanr(y, cross_val_predict(m, X, y, cv=cv)).statistic)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feature-version", default="det+obs-v1")
    ap.add_argument("--permute", type=int, default=100)
    a = ap.parse_args()

    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    df = load(conn, a.feature_version)
    has_sem = df["narrative_completeness"].notna().sum() > 30
    has_obs = df["num_speakers"].notna().sum() > 30
    edit = EDIT_DET + (EDIT_SEM if has_sem else []) + ((OBS_NUM + OBS_BOOL) if has_obs else [])
    ctx, ncat = ["work_code", "chan_code", "duration_sec"], 2
    y = df["apv"].astype(float).values
    cv = KFold(5, shuffle=True, random_state=0)

    print(f"fv={a.feature_version} n={len(df)} has_sem={has_sem} has_obs={has_obs} "
          f"edit_cols={len(edit)} perm={a.permute}")
    rc = cvr(df, ctx, ncat, y, cv)
    rf = cvr(df, ctx + edit, ncat, y, cv)
    up = rf - rc
    hits = 0
    for s in range(a.permute):
        d2 = df.copy()
        idx = np.random.default_rng(s).permutation(len(d2))
        for col in edit:
            d2[col] = d2[col].to_numpy()[idx]
        hits += (cvr(d2, ctx + edit, ncat, y, cv) - rc) >= up
    p = (hits + 1) / (a.permute + 1)
    print(f"target=apv(+14d)  ctx={rc:.3f}  full={rf:.3f}  uplift={up:+.3f}  perm_p={p:.3f}")
    print("해석: uplift>0 & perm_p<0.05 → 편집/관찰 피처가 작품·채널·길이 너머 성과 설명 = moat 신호.")


if __name__ == "__main__":
    main()
