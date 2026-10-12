#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Model fraud scoring (XGBoost) untuk prioritasi alert FDS  -- v3
Jalankan: python fds_scoring_v3.py
Output  : output/v3/{scores_v3.csv, model_v3.json, model_card.json}
Deterministik: seed tetap, n_jobs=1, tree_method='hist', data diurutkan by key1.
"""
import itertools
import json
import os
import random

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import average_precision_score, roc_auc_score

# ----------------------------------------------------------------- konfigurasi
SEED = 42
random.seed(SEED)
np.random.seed(SEED)

DATA = "/home/cdsw/parquet_output/result/trx_all_enriched_v2.parquet"
OUT = "output/v3"
os.makedirs(OUT, exist_ok=True)

TRAIN_M = ["2026-01", "2026-02", "2026-03", "2026-04"]
VAL_M = ["2026-05"]
TEST_M = ["2026-06"]
DEV_M = TRAIN_M + VAL_M

MIN_FEATURES = 6
CORR_THR = 0.85
N_PERM_REPEATS = 20
N_BOOT = 2000
MIN_FLAGGED = 30
PCT_GRID = list(range(30, 41))  # top 30..40%

BASE_FEATS = [
    # KYC
    "account_age", "flag_account_age_lt9", "flag_anomaly_income", "monthly_income",
    # Rule
    "n_sameday_pass_3h_l3m", "n_transfer_l3m", "n_transfer_l6m", "n_crypto",
    "n_in_10x_income", "n_out_10x_income", "n_new_beneficiary", "dpn_flag",
    "screening_flag",
    # Transaksi
    "n_trx_l3m", "total_amount_l3m", "avg_amount_l3m", "max_amount_l3m",
    "total_amount_out_l3m", "total_amount_in_l3m", "n_unique_counterparty_l3m",
    "n_channel_l3m", "n_failed_trx_l3m", "max_counterparty_popularity_l3m",
]
FLAG_COLS = ["flag_account_age_lt9", "flag_anomaly_income", "dpn_flag",
             "screening_flag", "flag_income_missing"]

GROUPS = {
    "KYC": ["account_age", "flag_account_age_lt9", "flag_anomaly_income",
            "monthly_income", "employment_tenure_m", "flag_income_missing"],
    "Rule": ["n_sameday_pass_3h_l3m", "n_transfer_l3m", "n_transfer_l6m", "n_crypto",
             "n_in_10x_income", "n_out_10x_income", "n_new_beneficiary", "dpn_flag",
             "screening_flag"],
    "Transaksi": ["n_trx_l3m", "total_amount_l3m", "avg_amount_l3m", "max_amount_l3m",
                  "total_amount_out_l3m", "total_amount_in_l3m",
                  "n_unique_counterparty_l3m", "n_channel_l3m", "n_failed_trx_l3m",
                  "max_counterparty_popularity_l3m"],
    "Derived": ["fe_out_to_income", "fe_in_to_income", "fe_maxamt_to_income",
                "fe_flow_balance", "fe_failed_ratio", "fe_trx_per_counterparty",
                "fe_total_flags_on"],
}

LEAKAGE = {"customername", "ktp", "npwp", "disposition_class"}


# ------------------------------------------------------------------ util dasar
def sdiv(a, b):
    """Pembagian aman: denominator <=0 / null -> NaN."""
    a = pd.to_numeric(a, errors="coerce")
    b = pd.to_numeric(b, errors="coerce")
    out = a / b.where(b > 0)
    return out.replace([np.inf, -np.inf], np.nan)


def build_features(df):
    df = df.copy()
    inc = pd.to_numeric(df["monthly_income"], errors="coerce")
    df["flag_income_missing"] = (inc.isna() | (inc <= 0)).astype(int)

    df["fe_out_to_income"] = sdiv(df["total_amount_out_l3m"], inc * 3)
    df["fe_in_to_income"] = sdiv(df["total_amount_in_l3m"], inc * 3)
    df["fe_maxamt_to_income"] = sdiv(df["max_amount_l3m"], inc)

    o, i = df["total_amount_out_l3m"], df["total_amount_in_l3m"]
    df["fe_flow_balance"] = sdiv(np.minimum(i, o), np.maximum(i, o))
    df["fe_failed_ratio"] = sdiv(df["n_failed_trx_l3m"], df["n_trx_l3m"])
    df["fe_trx_per_counterparty"] = sdiv(df["n_trx_l3m"], df["n_unique_counterparty_l3m"])

    flags = df[FLAG_COLS].apply(pd.to_numeric, errors="coerce").fillna(0)
    df["fe_total_flags_on"] = (flags > 0).sum(axis=1)

    # employment_start_date -> lama bekerja (bulan) relatif akhir bulan alert
    ref = pd.to_datetime(df["alert_month"] + "-01") + pd.offsets.MonthEnd(0)
    emp = pd.to_datetime(df["employment_start_date"], errors="coerce")
    ten = (ref - emp).dt.days / 30.4375
    df["employment_tenure_m"] = ten.where(ten >= 0)
    return df


def all_candidate_features():
    feats = [f for f in BASE_FEATS if f != "employment_start_date"]
    feats += ["employment_tenure_m", "flag_income_missing"]
    feats += GROUPS["Derived"]
    return list(dict.fromkeys(feats))


def make_model(p):
    return xgb.XGBClassifier(
        objective="binary:logistic", eval_metric="auc", tree_method="hist",
        n_estimators=p["n_estimators"], max_depth=p["max_depth"],
        learning_rate=p["learning_rate"], min_child_weight=p["min_child_weight"],
        subsample=p["subsample"], colsample_bytree=p["colsample_bytree"],
        reg_lambda=p["reg_lambda"], n_jobs=1, random_state=SEED, verbosity=0,
    )  # TANPA scale_pos_weight


DEFAULT_P = dict(n_estimators=200, max_depth=3, learning_rate=0.05,
                 min_child_weight=5, subsample=0.8, colsample_bytree=0.8,
                 reg_lambda=5.0)


def safe_auc(y, s):
    return roc_auc_score(y, s) if len(np.unique(y)) == 2 else np.nan


def safe_pr(y, s):
    return average_precision_score(y, s) if len(np.unique(y)) == 2 else np.nan


def cv_folds(df):
    """Rolling-origin: train 01..k -> validasi k+1 (hanya dalam DEV, test tak disentuh)."""
    m = DEV_M
    return [(m[:k], [m[k]]) for k in range(2, len(m))]


def split_xy(df, months, feats):
    d = df[df["alert_month"].isin(months)]
    return d[feats], d["y"].values, d


def cv_run(df, feats, params, return_oof=False):
    res, oof = [], []
    for tr_m, va_m in cv_folds(df):
        Xtr, ytr, _ = split_xy(df, tr_m, feats)
        Xva, yva, dva = split_xy(df, va_m, feats)
        mdl = make_model(params).fit(Xtr, ytr)
        s = mdl.predict_proba(Xva)[:, 1]
        res.append(dict(train="+".join(tr_m), valid=va_m[0], n_valid=len(yva),
                        auc=safe_auc(yva, s), pr_auc=safe_pr(yva, s)))
        if return_oof:
            oof.append(pd.DataFrame({"key1": dva["key1"].values, "fold": va_m[0],
                                     "y": yva, "score": s}))
    out = pd.DataFrame(res)
    return (out, pd.concat(oof, ignore_index=True)) if return_oof else out


# ------------------------------------------------------------- seleksi fitur
def univariate_auc(df, feats, months):
    d = df[df["alert_month"].isin(months)]
    r = {}
    for f in feats:
        x = d[f].fillna(d[f].median())
        a = safe_auc(d["y"].values, x.values)
        r[f] = max(a, 1 - a) if not np.isnan(a) else 0.5
    return pd.Series(r)


def select_features(df, feats):
    log = {}
    train = df[df["alert_month"].isin(TRAIN_M)]

    # 1) drop konstan
    const = [f for f in feats if train[f].nunique(dropna=True) <= 1]
    feats = [f for f in feats if f not in const]
    log["dropped_constant"] = const

    # 2) cluster Spearman |corr|>0.85 -> pertahankan fitur AUC univariat tertinggi
    uni = univariate_auc(df, feats, TRAIN_M).sort_values(ascending=False)
    corr = train[feats].corr(method="spearman").abs()
    kept, dropped_corr = [], {}
    for f in uni.index:
        clash = [k for k in kept if corr.loc[f, k] > CORR_THR]
        if clash:
            dropped_corr[f] = clash[0]
        else:
            kept.append(f)
    feats = [f for f in feats if f in kept]
    log["dropped_corr_cluster"] = dropped_corr

    # 3) permutation importance OOT (rata-rata antar fold), eliminasi bertahap
    def perm_importance(cur):
        rng = np.random.RandomState(SEED)
        imp = {f: [] for f in cur}
        for tr_m, va_m in cv_folds(df):
            Xtr, ytr, _ = split_xy(df, tr_m, cur)
            Xva, yva, _ = split_xy(df, va_m, cur)
            mdl = make_model(DEFAULT_P).fit(Xtr, ytr)
            base = safe_auc(yva, mdl.predict_proba(Xva)[:, 1])
            for f in cur:
                drops = []
                for _ in range(N_PERM_REPEATS):
                    Xp = Xva.copy()
                    Xp[f] = rng.permutation(Xp[f].values)
                    drops.append(base - safe_auc(yva, mdl.predict_proba(Xp)[:, 1]))
                imp[f].append(np.mean(drops))
        return pd.Series({f: np.mean(v) for f, v in imp.items()}).sort_values()

    cur = list(feats)
    elim = []
    while True:
        imp = perm_importance(cur)
        if len(cur) <= MIN_FEATURES or imp.iloc[0] > 0:
            break
        worst = imp.index[0]
        elim.append(worst)
        cur.remove(worst)
    log["dropped_perm_importance"] = elim
    log["final_perm_importance"] = imp.sort_values(ascending=False).round(5).to_dict()
    return cur, log


# ------------------------------------------------------------ hyperparameter
def tune(df, feats):
    grid = dict(max_depth=[2, 3, 4], learning_rate=[0.05, 0.08],
                min_child_weight=[5, 8, 10], n_estimators=[100, 200, 300],
                reg_lambda=[1.0, 5.0])
    keys = list(grid)
    rows = []
    for vals in itertools.product(*[grid[k] for k in keys]):
        p = dict(DEFAULT_P)
        p.update(dict(zip(keys, vals)))
        r = cv_run(df, feats, p)
        rows.append({**{k: p[k] for k in keys}, "cv_auc": r["auc"].mean(),
                     "cv_auc_std": r["auc"].std(ddof=0)})
    res = pd.DataFrame(rows).sort_values(
        ["cv_auc", "max_depth", "n_estimators"], ascending=[False, True, True]
    ).reset_index(drop=True)
    best = dict(DEFAULT_P)
    best.update({k: res.loc[0, k].item() if hasattr(res.loc[0, k], "item") else res.loc[0, k]
                 for k in keys})
    return best, res


# ------------------------------------------------------------------ evaluasi
def fbeta(y, flag, beta=0.5):
    tp = ((flag == 1) & (y == 1)).sum()
    fp = ((flag == 1) & (y == 0)).sum()
    fn = ((flag == 0) & (y == 1)).sum()
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return (1 + beta**2) * p * r / (beta**2 * p + r) if p + r else 0.0


def flag_top_pct(score, pct):
    n = len(score)
    k = int(np.ceil(n * pct / 100))
    order = np.argsort(-score, kind="mergesort")
    flag = np.zeros(n, dtype=int)
    flag[order[:k]] = 1
    return flag


def pick_threshold(oof):
    rows = []
    for pct in PCT_GRID:
        f1s, nfl = [], 0
        for _, g in oof.groupby("fold"):
            fl = flag_top_pct(g["score"].values, pct)
            f1s.append(fbeta(g["y"].values, fl))
            nfl += fl.sum()
        rows.append(dict(top_pct=pct, f05=np.mean(f1s), n_flagged_oot=int(nfl)))
    t = pd.DataFrame(rows)
    ok = t[t["n_flagged_oot"] >= MIN_FLAGGED]
    ok = ok if len(ok) else t
    best = int(ok.sort_values(["f05", "top_pct"], ascending=[False, True]).iloc[0]["top_pct"])
    return best, t


def lift_table(y, s, bins=10):
    order = np.argsort(-s, kind="mergesort")
    y_s = np.asarray(y)[order]
    base = y_s.mean()
    rows, cum = [], 0
    for i, idx in enumerate(np.array_split(np.arange(len(y_s)), bins), 1):
        pos = y_s[idx].sum()
        cum += pos
        rows.append(dict(bin=i, n=len(idx), n_pos=int(pos),
                         precision=pos / len(idx), lift=(pos / len(idx)) / base,
                         cum_recall=cum / y_s.sum()))
    return pd.DataFrame(rows)


def psi(expected, actual, bins=10):
    e = pd.Series(expected).dropna().values
    a = pd.Series(actual).dropna().values
    if len(e) < 20 or len(a) < 5 or np.unique(e).size < 2:
        return np.nan
    edges = np.unique(np.quantile(e, np.linspace(0, 1, bins + 1)))
    edges[0], edges[-1] = -np.inf, np.inf
    pe = np.histogram(e, edges)[0] / len(e)
    pa = np.histogram(a, edges)[0] / len(a)
    pe, pa = np.clip(pe, 1e-4, None), np.clip(pa, 1e-4, None)
    return float(np.sum((pa - pe) * np.log(pa / pe)))


def bootstrap_ci(y, s, fn, n=N_BOOT, seed=SEED):
    rng = np.random.RandomState(seed)
    y, s = np.asarray(y), np.asarray(s)
    vals = []
    for _ in range(n):
        ix = rng.randint(0, len(y), len(y))
        if len(np.unique(y[ix])) < 2:
            continue
        vals.append(fn(y[ix], s[ix]))
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def reason_codes(model, X, feats, topk=3):
    booster = model.get_booster()
    dm = xgb.DMatrix(X, feature_names=feats)
    contrib = booster.predict(dm, pred_contribs=True)[:, :-1]
    out = []
    for r in range(len(X)):
        pos = np.argsort(-contrib[r], kind="mergesort")[:topk]
        out.append([f"{feats[j]}={X.iloc[r, j]:.4g} (+{contrib[r, j]:.2f})"
                    for j in pos if contrib[r, j] > 0])
    return out


# ------------------------------------------------------------------- main
def main():
    raw = pd.read_parquet(DATA)
    assert not (LEAKAGE & set(BASE_FEATS)), "leakage di whitelist"
    raw = raw.sort_values("key1").reset_index(drop=True)
    raw["y"] = (raw["disposition_class"].astype(str).str.lower() == "suspicious").astype(int)
    assert raw["key1"].is_unique, "key1 harus unik"
    df = build_features(raw)

    print(f"Alert: {len(df)} | base rate: {df['y'].mean():.3f} | akun unik: {df['accountid'].nunique()}")
    print(df.groupby("alert_month")["y"].agg(["size", "mean"]).round(3).to_string())

    # --- seleksi fitur
    cand = all_candidate_features()
    feats, sel_log = select_features(df, cand)
    print("\n== SELEKSI FITUR ==")
    for k, v in sel_log.items():
        print(k, ":", v)

    # --- hyperparameter
    best_p, tune_res = tune(df, feats)
    print("\n== HYPERPARAMETER TERBAIK ==\n", best_p)
    print(tune_res.head(5).round(4).to_string(index=False))

    # --- CV per fold + OOF
    cv_res, oof = cv_run(df, feats, best_p, return_oof=True)
    print("\n== ROLLING-ORIGIN CV (AUC & PR-AUC per fold) ==")
    print(cv_res.round(4).to_string(index=False))
    auc_oot = float(cv_res["auc"].mean())
    auc_val05 = float(cv_res.loc[cv_res["valid"] == "2026-05", "auc"].iloc[0])

    # --- threshold
    top_pct, thr_tbl = pick_threshold(oof)
    print("\n== THRESHOLD (rank-percentile, F0.5 OOT) ==")
    print(thr_tbl.round(4).to_string(index=False))
    print("Dipilih: top", top_pct, "%")

    print("\n== LIFT TABLE OOT (pooled CV) ==")
    print(lift_table(oof["y"].values, oof["score"].values, 10).round(3).to_string(index=False))

    # --- ablation per grup (hapus 1 grup dari fitur final)
    abl = {"full": auc_oot}
    for g, cols in GROUPS.items():
        rest = [f for f in feats if f not in cols]
        if len(rest) >= 1 and len(rest) < len(feats):
            abl[f"minus_{g}"] = float(cv_run(df, rest, best_p)["auc"].mean())
    print("\n== ABLATION (mean OOT AUC) ==")
    for k, v in abl.items():
        print(f"{k:18s} {v:.4f}")

    # --- model final: fit di DEV (train+val), test dipakai SEKALI
    Xdev, ydev, ddev = split_xy(df, DEV_M, feats)
    final = make_model(best_p).fit(Xdev, ydev)
    Xte, yte, dte = split_xy(df, TEST_M, feats)
    s_te = final.predict_proba(Xte)[:, 1]
    auc_te = safe_auc(yte, s_te)
    pr_te = safe_pr(yte, s_te)
    ci_auc = bootstrap_ci(yte, s_te, safe_auc)
    ci_pr = bootstrap_ci(yte, s_te, safe_pr)
    fl_te = flag_top_pct(s_te, top_pct)
    tp = int(((fl_te == 1) & (yte == 1)).sum())
    prec_te = tp / max(fl_te.sum(), 1)
    rec_te = tp / max(yte.sum(), 1)

    # AUC akun baru (akun di test yang tidak pernah muncul di DEV)
    seen = set(ddev["accountid"])
    new_mask = ~dte["accountid"].isin(seen).values
    auc_new = safe_auc(yte[new_mask], s_te[new_mask]) if new_mask.sum() > 10 else np.nan

    # PSI fitur (train vs test) + skor (dev in-sample vs test)
    psi_feat = {f: psi(df.loc[df["alert_month"].isin(TRAIN_M), f], dte[f]) for f in feats}
    s_dev = final.predict_proba(Xdev)[:, 1]
    psi_score = psi(s_dev, s_te)

    print("\n== TEST 2026-06 (diintip sekali) ==")
    print(f"AUC {auc_te:.4f}  CI95 [{ci_auc[0]:.4f}, {ci_auc[1]:.4f}]")
    print(f"PR-AUC {pr_te:.4f}  CI95 [{ci_pr[0]:.4f}, {ci_pr[1]:.4f}]")
    print(f"Top {top_pct}% flagged={int(fl_te.sum())} precision={prec_te:.3f} recall={rec_te:.3f}")
    print(f"AUC akun baru (n={int(new_mask.sum())}): {auc_new}")
    print("PSI skor:", round(psi_score, 4))
    print("PSI fitur:", {k: round(v, 3) for k, v in psi_feat.items()})
    print("\nLift table test (5 bin):")
    print(lift_table(yte, s_te, 5).round(3).to_string(index=False))

    # --- skor semua alert + reason code
    X_all = df[feats]
    score_all = final.predict_proba(X_all)[:, 1]
    rc = reason_codes(final, X_all, feats)
    out = df[["key1", "accountid", "alert_month", "y"]].copy()
    out["split"] = np.where(out["alert_month"].isin(TRAIN_M), "train",
                    np.where(out["alert_month"].isin(VAL_M), "validate", "test"))
    out["score"] = score_all
    out["rank_in_month"] = out.groupby("alert_month")["score"].rank(
        ascending=False, method="first").astype(int)
    out["flag_priority"] = 0
    for m, g in out.groupby("alert_month"):
        out.loc[g.index, "flag_priority"] = flag_top_pct(g["score"].values, top_pct)
    for j in range(3):
        out[f"reason_{j+1}"] = [r[j] if len(r) > j else "" for r in rc]
    out.to_csv(f"{OUT}/scores_v3.csv", index=False)
    final.save_model(f"{OUT}/model_v3.json")

    # --- model card
    card = dict(
        version="v3", seed=SEED, xgboost=xgb.__version__,
        data=dict(file=DATA, n_alert=len(df), base_rate=float(df["y"].mean()),
                  n_account=int(df["accountid"].nunique())),
        split=dict(train=TRAIN_M, validate=VAL_M, test=TEST_M),
        features_final=feats, feature_selection=sel_log, params=best_p,
        cv=cv_res.round(5).to_dict("records"), auc_oot_mean_cv=auc_oot,
        auc_validate_2026_05=auc_val05,
        threshold=dict(type="rank_percentile", top_pct=top_pct,
                       table=thr_tbl.round(5).to_dict("records")),
        test=dict(auc=auc_te, auc_ci95=ci_auc, pr_auc=pr_te, pr_auc_ci95=ci_pr,
                  precision=prec_te, recall=rec_te, n_flagged=int(fl_te.sum()),
                  auc_new_accounts=None if np.isnan(auc_new) else float(auc_new),
                  n_new_accounts=int(new_mask.sum())),
        psi=dict(score=psi_score, features=psi_feat),
        ablation=abl,
        notes=["tanpa scale_pos_weight", "model final di-fit pada 2026-01..05",
               "skor split train/validate bersifat in-sample; gunakan CV/test untuk performa"],
    )
    with open(f"{OUT}/model_card.json", "w") as f:
        json.dump(card, f, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o))

    # --- ringkasan akhir
    print("\n" + "=" * 60)
    print("FITUR FINAL (%d): %s" % (len(feats), feats))
    print(f"AUC OOT (rata-rata CV)   : {auc_oot:.4f}  | validate 2026-05: {auc_val05:.4f}")
    print(f"AUC test                 : {auc_te:.4f}  CI95 [{ci_auc[0]:.4f}, {ci_auc[1]:.4f}]")
    print(f"Precision / Recall top {top_pct}%: {prec_te:.3f} / {rec_te:.3f}")
    print("\nTOP-5 ALERT TEST + REASON")
    top5 = out[out["split"] == "test"].sort_values("score", ascending=False).head(5)
    print(top5[["key1", "accountid", "score", "y", "reason_1", "reason_2", "reason_3"]]
          .round(4).to_string(index=False))


if __name__ == "__main__":
    main()
