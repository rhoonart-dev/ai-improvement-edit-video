#!/usr/bin/env python3
"""
리워드 모델 학습 — SPEC §3-2/§4/§5.

stage=baseline: 컨텍스트(작품·채널·길이·구독)로 +14일 성과를 OOF 예측 → 잔차(편집 기여분 proxy).
  전 클립(+14d) reward_scores 에 residual_by_work/channel·performance_score 적재. (baseline_B_v1)
stage=reward --feature-version FV: baseline 잔차(resid_apv)를 타깃으로 편집/의미 피처에서 R 학습 +
  permutation 귀인, reward_scores(rm_FV) 갱신(dim_retention·reward·attribution), 제어가능 피처 SHAP→directives.

실행: PIPELINE_DB_URL=... /Users/gimsewon/rhoonart/ai-video/.venv/bin/python scripts/train_reward_model.py --stage baseline
      ...                                                                          --stage reward --feature-version det-v1
의존: pandas scikit-learn scipy psycopg
"""
import argparse
import json
import math
import os

import numpy as np
import pandas as pd
import psycopg
from psycopg.rows import dict_row
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.model_selection import KFold, cross_val_predict

CTX_CAT = ["work_title", "channel_ext", "platform"]
CTX_NUM = ["duration_sec", "subscriber_count"]
EDIT_DET = ["cut_count", "avg_shot_len_sec", "cut_rhythm_var", "hook_timing_sec",
            "transition_count", "silence_ratio", "speech_ratio"]
EDIT_SEM = ["narrative_completeness", "climax_included", "hook_semantic_strength",
            "dialogue_density", "action_density"]
OBS_NUM = ["num_speakers", "face_count_first3s", "topic_shift_count"]      # 관찰형 수치(raw_features jsonb)
OBS_BOOL = ["conflict_present", "has_punchline", "question_hook"]          # 관찰형 불리언(raw_features)
DIR_THRESHOLD = 0.05   # R OOF rho 이 값 초과일 때만 디렉티브 생성(신호 없으면 안 만듦)


def z(s):
    s = pd.to_numeric(s, errors="coerce")
    sd = s.std(ddof=0)
    return (s - s.mean()) / sd if sd and sd > 0 else s * 0.0


def eb_resid(df, group, target, kappa=20.0):
    y = pd.to_numeric(df[target], errors="coerce")
    g = df[group].fillna("__NA__")
    st = y.groupby(g).agg(["mean", "count"])
    mu = (st["count"] * st["mean"] + kappa * y.mean()) / (st["count"] + kappa)
    return y - g.map(mu)


def _f(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)


def load_all(conn):
    # DISTINCT ON: v_training_matrix 는 feature_version 마다 행이 생겨 다중버전 클립이 중복됨 → 클립당 1행.
    df = pd.read_sql(
        "SELECT DISTINCT ON (clip_id) clip_id, work_title, channel_ext, platform, duration_sec, "
        "subscriber_count, views, avg_view_pct FROM v_training_matrix "
        "WHERE views IS NOT NULL AND is_format_short ORDER BY clip_id",
        conn,
    )
    df["lnviews"] = np.log1p(pd.to_numeric(df["views"], errors="coerce"))
    df["apv"] = pd.to_numeric(df["avg_view_pct"], errors="coerce")
    for c in CTX_CAT:
        df[c + "_code"] = df[c].fillna("__NA__").astype("category").cat.codes
    return df


def fit_baseline(df):
    """B: 컨텍스트 OOF 예측 → resid_apv/resid_lnviews + 정규화 컬럼. df 에 컬럼 추가, metrics 반환."""
    Xcols = [c + "_code" for c in CTX_CAT] + CTX_NUM
    X = df[Xcols].apply(pd.to_numeric, errors="coerce")
    cat = list(range(len(CTX_CAT)))
    cv = KFold(5, shuffle=True, random_state=0)
    metrics = {}
    for tgt in ["apv", "lnviews"]:
        d = df[df[tgt].notna()]
        oof = cross_val_predict(
            HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, categorical_features=cat),
            X.loc[d.index], d[tgt], cv=cv)
        df.loc[d.index, "resid_" + tgt] = d[tgt].to_numpy() - oof
        metrics[tgt + "_oof_rho"] = round(float(spearmanr(d[tgt], oof).statistic), 4)
    df["residual_by_work"] = eb_resid(df, "work_title", "apv")
    df["residual_by_channel"] = eb_resid(df, "channel_ext", "apv")
    df["performance_score"] = 0.6 * z(df["resid_apv"]).fillna(0) + 0.4 * z(df["resid_lnviews"]).fillna(0)
    return metrics


def upsert_version(conn, name, model_type, metrics, rows):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "INSERT INTO reward_model_versions(name,model_type,algorithm,training_rows,metrics,is_active,notes)"
            " VALUES(%s,%s,'histgbr',%s,%s,true,%s)"
            " ON CONFLICT (name) DO UPDATE SET trained_at=now(), metrics=EXCLUDED.metrics,"
            " training_rows=EXCLUDED.training_rows RETURNING id",
            (name, model_type, rows, json.dumps(metrics), f"{model_type} ({name})"))
        vid = cur.fetchone()["id"]
    conn.commit()
    return vid


def baseline_stage(conn):
    df = load_all(conn)
    metrics = fit_baseline(df)
    vid = upsert_version(conn, "baseline_B_v1", "baseline", metrics, len(df))
    rows = [(r.clip_id, vid, vid, _f(r.residual_by_work), _f(r.residual_by_channel), _f(r.performance_score))
            for r in df.itertuples()]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO reward_scores(clip_id,model_version_id,baseline_version_id,residual_by_work,"
            "residual_by_channel,performance_score,is_predicted) VALUES(%s,%s,%s,%s,%s,%s,false)"
            " ON CONFLICT (clip_id,model_version_id,is_predicted) DO UPDATE SET"
            " residual_by_work=EXCLUDED.residual_by_work, residual_by_channel=EXCLUDED.residual_by_channel,"
            " performance_score=EXCLUDED.performance_score", rows)
    conn.commit()
    print("baseline_B_v1:", metrics, "| rows", len(rows))


def reward_stage(conn, fv):
    df = load_all(conn)
    bmetrics = fit_baseline(df)
    base_id = upsert_version(conn, "baseline_B_v1", "baseline", bmetrics, len(df))

    feats = pd.read_sql(
        "SELECT clip_id, " + ", ".join(EDIT_DET + EDIT_SEM) +
        ", raw_features FROM clip_features WHERE feature_version = %s", conn, params=(fv,))
    # 관찰형(obs) 피처를 raw_features(jsonb)에서 수치/불리언으로 추출 (노트 "관찰 > 판단")
    obs = feats["raw_features"].apply(lambda r: r if isinstance(r, dict) else {})
    for c in OBS_NUM:
        feats[c] = pd.to_numeric(obs.apply(lambda d: d.get(c)), errors="coerce")
    for c in OBS_BOOL:
        feats[c] = obs.apply(lambda d: 1.0 if d.get(c) is True else (0.0 if d.get(c) is False else None))
    dff = df.merge(feats, on="clip_id", how="inner")
    dff = dff[dff["resid_apv"].notna()].reset_index(drop=True)
    has_sem = dff["narrative_completeness"].notna().sum() > 30
    has_obs = dff[OBS_NUM[0]].notna().sum() > 30
    cols = EDIT_DET + (EDIT_SEM if has_sem else []) + ((OBS_NUM + OBS_BOOL) if has_obs else [])
    X = dff[cols].apply(pd.to_numeric, errors="coerce")
    X = X.fillna(X.median(numeric_only=True)).fillna(0)
    y = dff["resid_apv"].to_numpy(dtype=float)

    cv = KFold(5, shuffle=True, random_state=0)
    oof = cross_val_predict(HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05), X, y, cv=cv)
    rho = float(spearmanr(y, oof).statistic)
    model = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05).fit(X, y)
    imp = permutation_importance(model, X, y, n_repeats=10, random_state=0)
    importances = {c: round(float(v), 5) for c, v in zip(cols, imp.importances_mean)}
    corrs = {c: round(float(pd.to_numeric(dff[c], errors="coerce").corr(pd.Series(y))), 4) for c in cols}
    metrics = {"R_oof_rho": round(rho, 4), "n": int(len(dff)), "has_sem": bool(has_sem),
               "has_obs": bool(has_obs), "importances": importances, "corrs": corrs}
    rm_id = upsert_version(conn, f"rm_{fv}", "reward", metrics, len(dff))

    dff["dim_retention"] = oof
    rows = [(r.clip_id, rm_id, base_id, _f(r.residual_by_work), _f(r.residual_by_channel),
             _f(r.performance_score), _f(r.dim_retention), _f(r.performance_score),
             json.dumps(importances)) for r in dff.itertuples()]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO reward_scores(clip_id,model_version_id,baseline_version_id,residual_by_work,"
            "residual_by_channel,performance_score,dim_retention,reward,attribution,is_predicted)"
            " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,false)"
            " ON CONFLICT (clip_id,model_version_id,is_predicted) DO UPDATE SET"
            " dim_retention=EXCLUDED.dim_retention, reward=EXCLUDED.reward, attribution=EXCLUDED.attribution,"
            " performance_score=EXCLUDED.performance_score", rows)
    conn.commit()
    print(f"rm_{fv}: R_oof_rho={rho:.4f} n={len(dff)} has_sem={has_sem} | reward_scores {len(rows)}")

    n_dir = gen_directives(conn, fv, rm_id, rho, importances, corrs)
    print(f"importances={importances}")
    print(f"directives generated: {n_dir}")


def gen_directives(conn, fv, rm_id, rho, importances, corrs):
    """제어가능 피처(feature_registry) 중 귀인 상위 → improvement_directives. 신호 없으면 0건."""
    if rho <= DIR_THRESHOLD:
        return 0
    reg = pd.read_sql(
        "SELECT name, controllable, control_surface FROM feature_registry WHERE controllable = true", conn)
    ctrl = dict(zip(reg["name"], reg["control_surface"]))
    cand = sorted(((c, importances.get(c, 0)) for c in importances if c in ctrl),
                  key=lambda kv: kv[1], reverse=True)
    cand = [(c, v) for c, v in cand if v > 0]
    n = 0
    with conn.cursor() as cur:
        for c, v in cand[:8]:
            surf = (ctrl.get(c) or "").lower()
            dtype = "param_tune" if ("config" in surf or "appconfig" in surf) else \
                    ("prompt_fix" if ("prompt" in surf or "story" in surf or "select" in surf) else "finetune")
            corr = corrs.get(c, 0.0)
            direction = "increase" if corr > 0 else "decrease"   # resid_apv 와 양/음
            cur.execute(
                "INSERT INTO improvement_directives(model_version_id,directive_type,target_feature,"
                "suggested_value,expected_delta,confidence,rationale,status)"
                " VALUES(%s,%s,%s,%s,%s,%s,%s,'proposed')",
                (rm_id, dtype, c, json.dumps({"direction": direction}), _f(v), _f(min(v * 5, 1.0)),
                 f"perm_importance={v}, corr(resid_apv)={corr}, surface={ctrl.get(c)}"))
            n += 1
    conn.commit()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="baseline", choices=["baseline", "reward"])
    ap.add_argument("--feature-version", default="det-v1")
    a = ap.parse_args()
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    if a.stage == "baseline":
        baseline_stage(conn)
    else:
        reward_stage(conn, a.feature_version)


if __name__ == "__main__":
    main()
