#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
FDS alert prioritisation model v3 (XGBoost).
Framing: PRIORITISASI ALERT (ranking/triage), bukan deteksi fraud absolut.
Label = penilaian analis -> mewarisi bias analis.

Run:
  /home/cdsw/.conda/envs/fraud/bin/python fds_alert_scoring_v3.py

Catatan desain:
- Hanya kolom whitelist yang dibaca dari parquet.
- Semua eksplorasi/seleksi (AUC univariat, redundansi, params, fitur, threshold)
  memakai DEV saja (2026-01..05). Test (2026-06) hanya dipakai di Fase 8, 1x.
"""
import os, json, re, itertools, warnings
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, average_precision_score
import xgboost as xgb

warnings.filterwarnings("ignore")
SEED = 42
PATH = "/home/cdsw/parquet_output/result/trx_all_enriched_v2.parquet"
OUT = "/home/cdsw/profiling_phase2/output/v3_2/"
os.makedirs(OUT, exist_ok=True)
pd.set_option("display.width", 220, "display.max_columns", 50, "display.max_rows", 200)

# Leakage radar: kolom yang sudah dikonfirmasi provenance-nya aman boleh ditaruh di sini
LEAK_CONFIRMED_OK = set()

# Fitur yang dikeluarkan manual dari pool model (keputusan berbasis OOT: importance sangat kecil)
EXCLUDE_MANUAL = ["n_channel_l3m"]

# Tier operasional berdasarkan SKOR MODEL ASLI (tanpa kalibrasi) per alert; alert datang per transaksi, jadi tidak ada top-% per bulan.
# Batas ini adalah titik awal; sesuaikan dengan kapasitas review dan hasil shadow mode.
TIER_CUTS = dict(tinggi=0.80, sedang=0.40)

# Stability selection (Fase 6): ulangi permutation importance dengan beberapa seed.
STAB_SEEDS, STAB_MIN_FREQ, STAB_NREP = 10, 0.7, 5

def hdr(t): print("\n" + "=" * 100 + f"\n{t}\n" + "=" * 100)
def save(df, name): df.to_csv(OUT + name, index=False); print(f"[saved] {name}")
def py(o):
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.floating,)): return None if np.isnan(o) else float(o)
    if isinstance(o, np.ndarray): return o.tolist()
    return str(o)

# ----------------------------------------------------------------------------
# Whitelist
# ----------------------------------------------------------------------------
KEY_COLS = ["key1", "accountid", "alert_month", "disposition_class", "flag",
            "monthly_income", "employment_start_date"]
GROUP_A = ["account_age", "flag_account_age_lt9", "flag_anomaly_income", "monthly_income"]
GROUP_B = ["n_sameday_pass_3h_l3m", "n_sameday_pass_3h_l6m", "n_sameday_pass_1h_l3m",
           "n_sameday_pass_1h_l6m", "n_transfer_l3m", "n_transfer_l6m",
           "n_transfer_not_normal_l3m", "transfer_not_normal_l3m_pct",
           "n_transfer_not_normal_l6m", "transfer_not_normal_l6m_pct",
           "n_crypto", "n_in_10x_income", "flag_in_10x_income", "n_out_10x_income",
           "flag_out_10x_income", "n_new_beneficiary", "flag_new_beneficiary",
           "dpn_flag", "screening_flag"]
GROUP_C = ["n_trx_l3m", "total_amount_l3m", "avg_amount_l3m", "max_amount_l3m",
           "total_amount_out_l3m", "total_amount_in_l3m", "n_unique_counterparty_l3m",
           "n_channel_l3m", "n_failed_trx_l3m", "max_counterparty_popularity_l3m",
           "avg_counterparty_popularity_l3m"]
POPULARITY = ["max_counterparty_popularity_l3m", "avg_counterparty_popularity_l3m"]
FLAG_COLS = ["flag_account_age_lt9", "flag_anomaly_income", "flag_in_10x_income",
             "flag_out_10x_income", "flag_new_beneficiary", "dpn_flag", "screening_flag"]
FE_COLS = ["fe_out_to_income", "fe_in_to_income", "fe_maxamt_to_income", "fe_flow_balance",
           "fe_failed_ratio", "fe_trx_per_counterparty", "fe_total_flags_on"]

# ----------------------------------------------------------------------------
# Load (whitelist only)
# ----------------------------------------------------------------------------
hdr("LOAD")
import pyarrow.parquet as pq
schema_cols = set(pq.read_schema(PATH).names)
wanted = KEY_COLS + GROUP_A + GROUP_B + GROUP_C
skipped = [c for c in wanted if c not in schema_cols]
use = [c for c in dict.fromkeys(wanted) if c in schema_cols]
print("Kolom whitelist tidak ada di data (di-skip):", skipped if skipped else "-")
df = pd.read_parquet(PATH, columns=use)
print("Raw shape (whitelist cols):", df.shape)

df = df[df["flag"].astype(str).str.strip() == "FDS"].copy().reset_index(drop=True)
df["y"] = (df["disposition_class"].astype(str).str.strip().str.lower() == "suspicious").astype(int)
df["m"] = df["alert_month"].astype(str).str[:7]
print(f"FDS alerts: {len(df)} | positif: {df.y.sum()} ({df.y.mean():.1%}) | "
      f"akun unik: {df.accountid.nunique()} | bulan: {sorted(df.m.unique())}")

# ----------------------------------------------------------------------------
# Parse income robust + tenure
# ----------------------------------------------------------------------------
def parse_income(v):
    if v is None or (isinstance(v, float) and np.isnan(v)): return np.nan
    if isinstance(v, (int, float, np.integer, np.floating)): return float(v)
    s = re.sub(r"[^0-9.,\-]", "", str(v).lower().replace("rp", ""))
    if s in ("", "-", ".", ","): return np.nan
    try:
        if "." in s and "," in s:
            if s.rfind(".") > s.rfind(","): s = s.replace(",", "")
            else: s = s.replace(".", "").replace(",", ".")
        elif s.count(".") > 1: s = s.replace(".", "")
        elif s.count(",") > 1: s = s.replace(",", "")
        elif "." in s and re.fullmatch(r"-?\d{1,3}\.\d{3}", s): s = s.replace(".", "")
        elif "," in s:
            s = s.replace(",", "") if re.fullmatch(r"-?\d{1,3},\d{3}", s) else s.replace(",", ".")
        return float(s)
    except Exception:
        return np.nan

raw_inc = df["monthly_income"].copy()
df["monthly_income"] = raw_inc.map(parse_income)
n_parse_fail = int((raw_inc.notna() & df["monthly_income"].isna()).sum())
df["flag_income_missing"] = (df["monthly_income"].isna() | (df["monthly_income"] <= 0)).astype(int)
inc_pos = df["monthly_income"].where(df["monthly_income"] > 0)  # pembagi aman

if "employment_start_date" in df.columns:
    amd = pd.to_datetime(df["m"] + "-01", errors="coerce")
    esd = pd.to_datetime(df["employment_start_date"], errors="coerce")
    ten = (amd - esd).dt.days.astype(float)
    df["employment_tenure_days"] = ten.where(ten >= 0)
else:
    print("employment_start_date tidak ada -> employment_tenure_days di-skip")

A_cols = [c for c in GROUP_A if c in df.columns] + \
         [c for c in ["flag_income_missing", "employment_tenure_days"] if c in df.columns]
B_cols = [c for c in GROUP_B if c in df.columns]
C_cols = [c for c in GROUP_C if c in df.columns]
BASE = A_cols + B_cols + C_cols
GROUP = {**{c: "A_KYC" for c in A_cols}, **{c: "B_rule" for c in B_cols},
         **{c: "C_trx" for c in C_cols}, **{c: "D_derived" for c in FE_COLS}}

# ----------------------------------------------------------------------------
# Split definitions (dev = train+val; test dilepas sampai Fase 8)
# ----------------------------------------------------------------------------
TRAIN_M = ["2026-01", "2026-02", "2026-03", "2026-04"]
VAL_M, TEST_M = "2026-05", "2026-06"
DEV = df[df.m <= VAL_M].copy()
TEST = df[df.m == TEST_M].copy()
print(f"Train(01-04)={df.m.isin(TRAIN_M).sum()} | Validate(05)={(df.m==VAL_M).sum()} | Test(06)={len(TEST)}")

# ----------------------------------------------------------------------------
# FASE 0 - Inventory
# ----------------------------------------------------------------------------
hdr("FASE 0 - INVENTORY")
rows = []
for c in BASE:
    s = df[c]; nun = s.nunique(dropna=True)
    note = []
    if nun < 2: note.append("KONSTAN")
    if s.isna().mean() > 0.90: note.append("MISSING>90%")
    if nun == 2 and s.value_counts(normalize=True).min() < 0.01: note.append("BINER_KELAS<1%")
    rows.append(dict(feature=c, group=GROUP[c], dtype=str(s.dtype), n_missing=int(s.isna().sum()),
                     pct_missing=round(100 * s.isna().mean(), 2), n_unique=int(nun),
                     sample_values=" | ".join(map(str, s.dropna().unique()[:3])), flags=";".join(note)))
inv = pd.DataFrame(rows); print(inv.to_string(index=False)); save(inv, "inventory_v3.csv")

# ----------------------------------------------------------------------------
# FASE 1 - Sanity check
# ----------------------------------------------------------------------------
hdr("FASE 1 - SANITY CHECK")
dq = []
def chk(name, mask):
    p = 100 * mask.mean(); dq.append(dict(check=name, pct=round(p, 3)))
    print(f"{'[ANOMALI >1%] ' if p > 1 else ''}{name}: {p:.3f}%")
if {"n_transfer_l3m", "n_trx_l3m"} <= set(df.columns):
    chk("n_transfer_l3m > n_trx_l3m", df.n_transfer_l3m > df.n_trx_l3m)
if {"total_amount_l3m", "total_amount_out_l3m", "total_amount_in_l3m"} <= set(df.columns):
    t = df.total_amount_l3m; chk("|total - (out+in)| > 1%",
        (t - (df.total_amount_out_l3m + df.total_amount_in_l3m)).abs() > 0.01 * t.abs())
if {"avg_amount_l3m", "n_trx_l3m", "total_amount_l3m"} <= set(df.columns):
    t = df.total_amount_l3m; chk("|avg*n - total| > 1%", (df.avg_amount_l3m * df.n_trx_l3m - t).abs() > 0.01 * t.abs())
print(f"Duplikat key1: {int(df.key1.duplicated().sum())}")
print(f"monthly_income: n_missing(raw)={int(raw_inc.isna().sum())} | n<=0={int((df.monthly_income<=0).sum())} "
      f"| gagal parse={n_parse_fail}")
print("5 nilai raw paling sering:\n", raw_inc.value_counts(dropna=False).head(5).to_string())
save(pd.DataFrame(dq), "data_quality_v3.csv")

# ----------------------------------------------------------------------------
# FASE 3 (dibuat dulu supaya fungsi explore bisa dipakai 2x) - Feature engineering
# ----------------------------------------------------------------------------
def sdiv(a, b): return (a / b.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
def add_fe(d):
    inc = d["monthly_income"].where(d["monthly_income"] > 0)
    out_, in_ = d.get("total_amount_out_l3m"), d.get("total_amount_in_l3m")
    if out_ is not None: d["fe_out_to_income"] = sdiv(out_, inc * 3)
    if in_ is not None: d["fe_in_to_income"] = sdiv(in_, inc * 3)
    if "max_amount_l3m" in d: d["fe_maxamt_to_income"] = sdiv(d["max_amount_l3m"], inc)
    if out_ is not None and in_ is not None:
        d["fe_flow_balance"] = sdiv(pd.concat([in_, out_], axis=1).min(axis=1),
                                    pd.concat([in_, out_], axis=1).max(axis=1))
    if {"n_failed_trx_l3m", "n_trx_l3m"} <= set(d.columns): d["fe_failed_ratio"] = sdiv(d.n_failed_trx_l3m, d.n_trx_l3m)
    if {"n_trx_l3m", "n_unique_counterparty_l3m"} <= set(d.columns):
        d["fe_trx_per_counterparty"] = sdiv(d.n_trx_l3m, d.n_unique_counterparty_l3m)
    fl = [c for c in FLAG_COLS if c in d.columns]
    d["fe_total_flags_on"] = d[fl].fillna(0).gt(0).sum(axis=1).astype(float)
    return d
df = add_fe(df); DEV = df[df.m <= VAL_M].copy(); TEST = df[df.m == TEST_M].copy()
FE_PRESENT = [c for c in FE_COLS if c in df.columns]
FULL = BASE + FE_PRESENT

# ----------------------------------------------------------------------------
# FASE 2 - Eksplorasi fitur (DEV saja)
# ----------------------------------------------------------------------------
def uni_auc(x, y):
    xf = x.fillna(x.median()) if x.notna().any() else x.fillna(0)
    if xf.nunique() < 2: return 0.5
    return roc_auc_score(y, xf)

def explore(d, feats, tag):
    hdr(f"FASE 2 - EKSPLORASI ({tag}) | n={len(d)}, fitur={len(feats)} | basis: DEV 2026-01..05")
    y = d["y"].values; info = {}
    for c in feats:
        s = d[c]; a = uni_auc(s, y)
        info[c] = dict(feature=c, group=GROUP[c], n_missing_pct=round(100 * s.isna().mean(), 2),
                       n_unique=int(s.nunique()), AUC_uni=a, strength=abs(a - 0.5))
    ex = pd.DataFrame(info).T.reset_index(drop=True)
    ex["AUC_uni"] = ex.AUC_uni.astype(float); ex["strength"] = ex.strength.astype(float)
    exs = ex.sort_values("strength", ascending=False)
    print("\n(a) TOP-15 |AUC-0.5|:\n", exs.head(15)[["feature", "group", "AUC_uni"]].to_string(index=False))
    print("\n(a) BOTTOM-15:\n", exs.tail(15)[["feature", "group", "AUC_uni"]].to_string(index=False))
    save(ex.sort_values("strength", ascending=False), f"univariate_auc_{tag}_v3.csv")

    # (b) leakage radar
    leak = ex[((ex.AUC_uni > 0.80) | (ex.AUC_uni < 0.20)) & (~ex.feature.isin(LEAK_CONFIRMED_OK))]
    print("\n(b) LEAKAGE RADAR (AUC>0.80 atau <0.20):", "tidak ada" if leak.empty else "")
    for c in leak.feature:
        print(f"  WASPADA {c}: AUC={ex.set_index('feature').loc[c,'AUC_uni']:.3f} | contoh={d[c].dropna().unique()[:5].tolist()}"
              f"  -> mohon konfirmasi provenance (default: DROP_LEAKAGE; whitelist lewat LEAK_CONFIRMED_OK)")
    leakset = set(leak.feature)

    # (d) konstan / missing
    drop_const = set(ex[ex.n_unique < 2].feature)
    drop_miss = set(ex[ex.n_missing_pct > 90].feature)
    eligible = [c for c in feats if c not in drop_const | drop_miss | leakset]

    # (c) redundansi Spearman, union-find
    X = d[eligible].apply(lambda s: s.fillna(s.median()) if s.notna().any() else s.fillna(0))
    rho = X.corr(method="spearman").abs().fillna(0)
    parent = {c: c for c in eligible}
    def find(a):
        while parent[a] != a: parent[a] = parent[parent[a]]; a = parent[a]
        return a
    for i, a in enumerate(eligible):
        for b in eligible[i + 1:]:
            if rho.loc[a, b] > 0.85: parent[find(a)] = find(b)
    comp = {}
    for c in eligible: comp.setdefault(find(c), []).append(c)
    auc_s = ex.set_index("feature")["strength"]
    clusters, cl_rows, cl_id = {}, [], 0
    dropped_red = set()
    print("\n(c) CLUSTER REDUNDANSI (|rho_spearman| > 0.85):")
    for members in comp.values():
        if len(members) < 2: continue
        cl_id += 1
        keep = max(members, key=lambda c: auc_s[c])
        print(f"  Cluster {cl_id}: keep={keep}")
        for c in members:
            clusters[c] = cl_id
            if c != keep: dropped_red.add(c)
            r = 1.0 if c == keep else rho.loc[c, keep]
            reason = "AUC univariat terkuat di cluster" if c == keep else f"|rho|={r:.2f} dgn {keep}; AUC lebih lemah"
            print(f"     {'KEEP ' if c==keep else 'DROP '} {c:40s} AUC={ex.set_index('feature').loc[c,'AUC_uni']:.3f}  {reason}")
            cl_rows.append(dict(cluster=cl_id, feature=c, group=GROUP[c], AUC_uni=ex.set_index("feature").loc[c, "AUC_uni"],
                                decision="KEEP" if c == keep else "DROP_REDUNDANT", rho_with_keeper=round(r, 3), reason=reason))
    if not cl_rows: print("  (tidak ada cluster)")
    red = pd.DataFrame(cl_rows, columns=["cluster", "feature", "group", "AUC_uni", "decision", "rho_with_keeper", "reason"])

    # (e) status
    def status(c):
        if c in drop_miss: return "DROP_MISSING"
        if c in drop_const: return "DROP_CONSTANT"
        if c in leakset: return "DROP_LEAKAGE"
        if c in dropped_red: return "DROP_REDUNDANT"
        if auc_s[c] < 0.02: return "DROP_WEAK"   # kandidat; baru diputuskan di Fase 6
        return "KEEP"
    ex["cluster"] = ex.feature.map(clusters)
    ex["status"] = ex.feature.map(status)
    out = ex[["feature", "group", "n_missing_pct", "n_unique", "AUC_uni", "cluster", "status"]]
    print("\n(e) RINGKASAN:\n", out.sort_values(["status", "feature"]).to_string(index=False))
    print("\nStatus count:", out.status.value_counts().to_dict())
    return out, red

summ0, red0 = explore(DEV, BASE, "base")
save(summ0, "exploration_summary_v3.csv"); save(red0, "redundancy_clusters_v3.csv")

hdr("FASE 3 - FEATURE ENGINEERING: " + ", ".join(FE_PRESENT))
summ, red = explore(DEV, FULL, "with_fe")
save(summ, "exploration_summary_with_fe_v3.csv"); save(red, "redundancy_clusters_with_fe_v3.csv")
POOL = summ[summ.status.isin(["KEEP", "DROP_WEAK"])].feature.tolist()  # weak ditentukan di Fase 6
POOL = [c for c in POOL if c not in EXCLUDE_MANUAL]
print("Dikeluarkan manual dari pool:", EXCLUDE_MANUAL)
print(f"\nPOOL kandidat model ({len(POOL)}): {POOL}")

# ----------------------------------------------------------------------------
# FASE 4 - Rolling-origin CV (DEV)
# ----------------------------------------------------------------------------
# Expanding window, sama dengan v4: tiap fold dilatih minimal 2 bulan, diuji di bulan berikutnya.
# (v3 lama: fold 1 hanya dilatih Jan dan April tidak pernah diuji.)
DEV_MONTHS = sorted(DEV.m.unique())
MIN_TRAIN_MONTHS = 2
FOLDS = [(DEV_MONTHS[:i], [DEV_MONTHS[i]]) for i in range(MIN_TRAIN_MONTHS, len(DEV_MONTHS))]
print("Rolling-origin folds:", [(f"{a[0]}..{a[-1]}", b[0]) for a, b in FOLDS])

def mk(p, seed=SEED):
    return xgb.XGBClassifier(n_estimators=p["n_estimators"], max_depth=p["max_depth"], learning_rate=p["lr"],
                             min_child_weight=p["min_child_weight"], reg_lambda=p["reg_lambda"],
                             subsample=0.7, colsample_bytree=0.7, eval_metric="auc", random_state=seed,
                             n_jobs=4, tree_method="hist", objective="binary:logistic")

def cv_run(feats, p, keep_models=False, seed=SEED):
    aucs, oot, models = [], [], []
    for trm, vam in FOLDS:
        tr, va = DEV[DEV.m.isin(trm)], DEV[DEV.m.isin(vam)]
        m = mk(p, seed).fit(tr[feats], tr.y)
        pr = m.predict_proba(va[feats])[:, 1]
        aucs.append(roc_auc_score(va.y, pr))
        oot.append(pd.DataFrame({"idx": va.index, "m": va.m.values, "y": va.y.values, "score": pr}))
        models.append((m, va))
    return aucs, pd.concat(oot), (models if keep_models else None)

# ----------------------------------------------------------------------------
# FASE 5 - Hyperparameter search
# ----------------------------------------------------------------------------
hdr("FASE 5 - HYPERPARAMETER SEARCH (AUC OOT rata-rata, rolling-origin)")
grid = list(itertools.product([200, 300], [2, 3, 4], [0.05, 0.08], [5, 10], [3, 10]))
srch = []
for ne, md, lr, mcw, rl in grid:
    p = dict(n_estimators=ne, max_depth=md, lr=lr, min_child_weight=mcw, reg_lambda=rl)
    a, _, _ = cv_run(POOL, p)
    srch.append({**p, "auc_f1": a[0], "auc_f2": a[1], "auc_f3": a[2], "auc_mean": np.mean(a), "auc_std": np.std(a)})
srch = pd.DataFrame(srch).sort_values("auc_mean", ascending=False).reset_index(drop=True)
print(srch.head(15).round(4).to_string(index=False)); save(srch, "hyperparam_search_v3.csv")
BEST = {k: srch.loc[0, k] for k in ["n_estimators", "max_depth", "lr", "min_child_weight", "reg_lambda"]}
BEST = {k: (int(v) if k != "lr" and k != "reg_lambda" else float(v)) for k, v in BEST.items()}
print("BEST:", BEST)

# ----------------------------------------------------------------------------
# FASE 6 - Seleksi fitur (permutation importance OOT)
# ----------------------------------------------------------------------------
hdr("FASE 6 - SELEKSI FITUR FINAL (parsimony)")
def perm_imp(models, feats, n_rep=10, seed=SEED):
    rng = np.random.RandomState(seed); res = np.zeros((len(models), len(feats)))
    for fi, (m, va) in enumerate(models):
        Xv, yv = va[feats].copy(), va.y.values
        base = roc_auc_score(yv, m.predict_proba(Xv)[:, 1])
        for j, c in enumerate(feats):
            drops = []
            for _ in range(n_rep):
                Xp = Xv.copy(); Xp[c] = rng.permutation(Xp[c].values)
                drops.append(base - roc_auc_score(yv, m.predict_proba(Xp)[:, 1]))
            res[fi, j] = np.mean(drops)
    return pd.Series(res.mean(axis=0), index=feats)

base_aucs, _, base_models = cv_run(POOL, BEST, keep_models=True)
base_auc = np.mean(base_aucs)
imp = perm_imp(base_models, POOL)
print(f"AUC OOT pool penuh = {base_auc:.4f}")
final, decisions = list(POOL), []
cands = imp[imp <= 0].sort_values().index.tolist()   # terburuk dulu
for c in cands:
    if len(final) <= 6:
        decisions.append((c, imp[c], "KEEP", "minimum 6 fitur tercapai")); continue
    trial = [f for f in final if f != c]
    a, _, _ = cv_run(trial, BEST); a = np.mean(a)
    if base_auc - a < 0.01:
        final = trial; decisions.append((c, imp[c], "DROP", f"imp<=0; AUC OOT {a:.4f} (turun {base_auc-a:+.4f} < 0.01)"))
    else:
        decisions.append((c, imp[c], "KEEP", f"imp<=0 tapi drop menurunkan AUC OOT >= 0.01 ({a:.4f})"))
for c in POOL:
    if c not in cands: decisions.append((c, imp[c], "KEEP", "imp OOT > 0"))
dec = pd.DataFrame(decisions, columns=["feature", "perm_importance_oot", "decision", "reason"]).sort_values("perm_importance_oot")
print(dec.round(5).to_string(index=False)); save(dec, "feature_decisions_v3.csv")
# --- Stability selection: ulangi permutation importance dengan beberapa seed (tree sampling + shuffle berbeda) ---
# Fitur dipertahankan bila importance OOT-nya positif di >= STAB_MIN_FREQ seed. Ini menggantikan keputusan satu-run di atas.
imps = []
for sd in range(STAB_SEEDS):
    _, _, mdl = cv_run(POOL, BEST, keep_models=True, seed=SEED + sd)
    imps.append(perm_imp(mdl, POOL, n_rep=STAB_NREP, seed=SEED + 100 + sd))
IM = pd.concat(imps, axis=1)
stab = pd.DataFrame({"feature": IM.index, "mean_imp": IM.mean(axis=1).values, "std_imp": IM.std(axis=1).values,
                     "freq_positive": (IM > 0).mean(axis=1).values})
stab["single_run_decision"] = stab.feature.map(dict(zip(dec.feature, dec.decision)))
stab["stable"] = stab.freq_positive >= STAB_MIN_FREQ
stab = stab.sort_values(["freq_positive", "mean_imp"], ascending=False).reset_index(drop=True)
stable_final = stab[stab.stable].feature.tolist()
by_imp = stab.sort_values("mean_imp", ascending=False).feature.tolist()
if len(stable_final) < 6:
    stable_final = by_imp[:6]
a_st = float(np.mean(cv_run(stable_final, BEST)[0]))
rest_f = [f for f in by_imp if f not in stable_final]
while base_auc - a_st >= 0.01 and rest_f:          # parsimony: AUC OOT tidak boleh turun >= 0.01 dari pool penuh
    stable_final.append(rest_f.pop(0)); a_st = float(np.mean(cv_run(stable_final, BEST)[0]))
stab["final"] = stab.feature.isin(stable_final)
print(f"\nSTABILITY SELECTION ({STAB_SEEDS} seed, min frekuensi {STAB_MIN_FREQ}):")
print(stab.round(4).to_string(index=False)); save(stab, "feature_stability_v3.csv")
print(f"AUC OOT set stabil ({len(stable_final)} fitur) = {a_st:.4f} vs pool penuh {base_auc:.4f}")
print("Beda dengan keputusan satu-run -> ditambah:", sorted(set(stable_final) - set(final)), "| dibuang:", sorted(set(final) - set(stable_final)))
FINAL = stable_final
oot_aucs, OOT, _ = cv_run(FINAL, BEST)
print(f"\nFITUR FINAL ({len(FINAL)}): {FINAL}")
print(f"AUC OOT final per fold: {np.round(oot_aucs,4).tolist()} | mean={np.mean(oot_aucs):.4f} ± {np.std(oot_aucs):.4f}")
print("Catatan: pakai fitur popularity? ", [c for c in POPULARITY if c in FINAL] or "tidak (lihat ablation)")

# ----------------------------------------------------------------------------
# FASE 7 - Threshold (OOT)
# ----------------------------------------------------------------------------
hdr("FASE 7 - THRESHOLD (OOT)")
def f05(p, r): return 0 if (p + r) == 0 else 1.25 * p * r / (0.25 * p + r)
OOT["rank_pct"] = OOT.groupby("m")["score"].rank(ascending=False, method="first") / OOT.groupby("m")["score"].transform("size")
def sim_grid(d, split):
    """Grid simulasi lengkap: threshold skor 0.05..0.95 dan top-% alert 5%..95% (langkah 5%)."""
    rows, P_, N_ = [], int(d.y.sum()), len(d)
    for mode in ["score_threshold", "top_pct"]:
        for lv in range(5, 100, 5):
            f = (d.score >= lv / 100) if mode == "score_threshold" else (d.rank_pct <= lv / 100)
            n = int(f.sum()); tp = int((f & (d.y == 1)).sum())
            pr = tp / n if n else np.nan; rc = tp / P_
            rows.append(dict(split=split, mode=mode, level=lv / 100, n_flag=n, pct_flag=n / N_, tp=tp,
                             n_total=N_, n_pos=P_, precision=pr, recall=rc,
                             F05=f05(pr, rc) if n else np.nan, lift=pr / (P_ / N_) if n else np.nan))
    return pd.DataFrame(rows)
thr = sim_grid(OOT, "oot")
print(thr.round(3).to_string(index=False)); save(thr, "threshold_oot_v3.csv")  # menimpa tabel lama
best_f05 = thr[(thr["mode"] == "score_threshold") & (thr.n_flag >= 30)].sort_values("F05", ascending=False).head(1)
print("\nBest F0.5 score-threshold (min 30 flag):\n", best_f05.round(3).to_string(index=False))
RANK_PCT = 0.30   # hanya untuk analisis kapasitas/pembanding; BUKAN aturan operasional (alert datang per transaksi)
def tier_of(sc): return np.where(np.asarray(sc) >= TIER_CUTS["tinggi"], "Tinggi", np.where(np.asarray(sc) >= TIER_CUTS["sedang"], "Sedang", "Rendah"))
def wilson(k, n, z=1.96):
    if n == 0: return (np.nan, np.nan)
    q = k / n; c = (q + z * z / (2 * n)) / (1 + z * z / n); h = z * np.sqrt(q * (1 - q) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0, c - h), min(1, c + h))
def tier_table(d, split):
    rows = []
    for t in ["Tinggi", "Sedang", "Rendah"]:
        m = (d["tier"] == t).values; n = int(m.sum()); k = int(d.y.values[m].sum()); lo, hi = wilson(k, n)
        rows.append(dict(split=split, tier=t, n=n, pct_alert=n / len(d), n_susp=k, susp_rate=k / n if n else np.nan,
                         ci95_lo=lo, ci95_hi=hi, share_of_all_susp=k / max(int(d.y.sum()), 1)))
    return pd.DataFrame(rows)
OOT["tier"] = tier_of(OOT.score.values)
tt_oot = tier_table(OOT, "oot"); print("\nRINGKASAN TIER (OOT):\n", tt_oot.round(3).to_string(index=False))
print(f"\nPILIHAN OPERASIONAL: skor model asli per alert -> tier (Tinggi >= {TIER_CUTS['tinggi']}, Sedang >= {TIER_CUTS['sedang']}, selain itu Rendah).\n"
      "Alasan: alert muncul per transaksi, sehingga keputusan harus bisa diambil dari skor satu alert tanpa menunggu alert lain.\n"
      "Skor TIDAK dikalibrasi dan tidak dibaca sebagai probabilitas. Tier mengatur URUTAN dan SLA review, bukan apakah alert di-review. Top-% per bulan hanya dipakai untuk analisis kapasitas.")

# ----------------------------------------------------------------------------
# FASE 8 - Evaluasi TEST (1x)
# ----------------------------------------------------------------------------
hdr("FASE 8 - EVALUASI TEST (2026-06) - 1x")
final_model = mk(BEST).fit(DEV[FINAL], DEV.y)
TEST = TEST.copy(); TEST["score"] = final_model.predict_proba(TEST[FINAL])[:, 1]
TEST["rank_pct"] = TEST.score.rank(ascending=False, method="first") / len(TEST)
TEST["tier"] = tier_of(TEST.score.values)
TEST["flag"] = TEST.tier.isin(["Tinggi", "Sedang"])   # flag operasional = tier Tinggi + Sedang
yt, st, ft = TEST.y.values, TEST.score.values, TEST.flag.values
auc_t, pr_t = roc_auc_score(yt, st), average_precision_score(yt, st)

rng = np.random.RandomState(SEED); bs = []
for _ in range(1000):
    i = rng.randint(0, len(yt), len(yt)); yy = yt[i]
    if yy.min() == yy.max(): continue
    f = ft[i]; nf = f.sum(); tp = (f & (yy == 1)).sum()
    bs.append((roc_auc_score(yy, st[i]), average_precision_score(yy, st[i]), nf,
               tp / nf if nf else np.nan, tp / yy.sum()))
bs = np.array(bs); ci = lambda k: np.nanpercentile(bs[:, k], [2.5, 97.5])
n_flag, tp = int(ft.sum()), int((ft & (yt == 1)).sum())
prec_t, rec_t = tp / n_flag, tp / yt.sum()
print(f"n_test={len(yt)} | positif={yt.sum()} ({yt.mean():.1%})")
print(f"AUC    = {auc_t:.4f}  CI95 [{ci(0)[0]:.4f}, {ci(0)[1]:.4f}]")
print(f"PR-AUC = {pr_t:.4f}  CI95 [{ci(1)[0]:.4f}, {ci(1)[1]:.4f}]  (base rate {yt.mean():.3f})")
print(f"@ flag (tier Tinggi+Sedang): n_flag={n_flag} (CI {ci(2)[0]:.0f}-{ci(2)[1]:.0f}) | "
      f"precision={prec_t:.3f} CI[{ci(3)[0]:.3f},{ci(3)[1]:.3f}] | recall={rec_t:.3f} CI[{ci(4)[0]:.3f},{ci(4)[1]:.3f}]")

# Tier di Test (1x)
tt_test = tier_table(TEST, "test"); print("\nRINGKASAN TIER (Test):\n", tt_test.round(3).to_string(index=False))
save(pd.concat([tt_oot, tt_test], ignore_index=True), "tier_summary_v3.csv")
f30 = (TEST.rank_pct <= RANK_PCT).values; tp30 = int((f30 & (yt == 1)).sum())
print(f"Pembanding (bukan operasional): top {int(RANK_PCT*100)}% Test = {int(f30.sum())} alert | precision {tp30/f30.sum():.3f} | recall {tp30/yt.sum():.3f}")

# Lift / gains
T = TEST.sort_values("score", ascending=False).reset_index(drop=True)
T["decile"] = (np.arange(len(T)) * 10 // len(T)) + 1
g = T.groupby("decile").agg(n=("y", "size"), n_susp=("y", "sum"), min_score=("score", "min"), max_score=("score", "max")).reset_index()
g["precision"] = g.n_susp / g.n; g["lift"] = g.precision / yt.mean()
g["cum_recall"] = g.n_susp.cumsum() / yt.sum(); g["cum_precision"] = g.n_susp.cumsum() / g.n.cumsum()
print("\nLIFT / GAINS (decile):\n", g.round(3).to_string(index=False)); save(g, "gains_test_v3.csv")

# Simulasi lengkap Test + data siap-pakai untuk halaman HTML (menimpa data lama)
TEST_GRID = sim_grid(TEST, "test"); save(TEST_GRID, "simulation_test_v3.csv")
print("\nSIMULASI TEST (threshold skor & top-%):\n", TEST_GRID.round(3).to_string(index=False))
# Tabel skor vs tingkat suspicious historis per rentang 0.1 (skor asli, tanpa kalibrasi)
cb = []
for nm_, d_ in [("test", TEST), ("oot", OOT)]:
    for b_ in range(10):
        lo_, hi_ = b_ / 10, (b_ + 1) / 10
        m_ = (d_.score >= lo_) & ((d_.score < hi_) if b_ < 9 else (d_.score <= 1))
        cb.append(dict(split=nm_, bin_lo=lo_, bin_hi=hi_, n=int(m_.sum()), n_susp=int(d_.y[m_].sum())))
save(pd.DataFrame(cb), "score_bins_v3.csv")
sim_json = {}
for nm, gdf in [("test", TEST_GRID), ("oot", thr)]:
    sim_json[nm] = {"n_total": int(gdf.n_total.iloc[0]), "n_pos": int(gdf.n_pos.iloc[0])}
    for mode in ["score_threshold", "top_pct"]:
        sub = gdf[gdf["mode"] == mode]
        sim_json[nm][mode] = [[float(a), int(b), int(c)] for a, b, c in zip(sub.level, sub.n_flag, sub.tp)]  # [level, n_flag, tp]
json.dump(sim_json, open(OUT + "simulation_data_v3.json", "w")); print("[saved] simulation_data_v3.json")
print("\nJSON_SIMULASI_BEGIN\n" + json.dumps(sim_json) + "\nJSON_SIMULASI_END")

# PSI
def psi(a, b):
    a, b = pd.Series(a), pd.Series(b)
    edges = np.unique(np.nanquantile(a.dropna(), np.linspace(0, 1, 11))) if a.notna().any() else np.array([])
    def dist(s):
        v = s.dropna().values; n = max(len(s), 1)
        if len(edges) >= 2:
            c = np.bincount(np.digitize(v, edges[1:-1], right=True), minlength=len(edges) - 1) / n
        else: c = np.array([len(v) / n])
        return np.append(c, s.isna().mean())
    p, q = np.clip(dist(a), 1e-4, None), np.clip(dist(b), 1e-4, None)
    return float(np.sum((q - p) * np.log(q / p)))
psi_df = pd.DataFrame({"feature": FINAL, "psi": [psi(DEV[c], TEST[c]) for c in FINAL]})
psi_df["status"] = np.where(psi_df.psi > 0.25, "DRIFT >0.25", np.where(psi_df.psi > 0.10, "waspada 0.10-0.25", "stabil"))
print("\nPSI (dev vs test):\n", psi_df.sort_values("psi", ascending=False).round(3).to_string(index=False)); save(psi_df, "psi_test_v3.csv")

# Akun baru
seen = set(DEV.accountid.unique()); new = TEST[~TEST.accountid.isin(seen)]
auc_new = np.nan
if len(new) >= 10 and new.y.nunique() == 2: auc_new = roc_auc_score(new.y, new.score)
print(f"\nAkun test yg tak pernah muncul di train/validate: {len(new)}/{len(TEST)} alert | AUC={auc_new}")

# Ablation per kelompok
abl = []
grp_of = lambda c: GROUP[c]
groups = {g_: [c for c in FINAL if grp_of(c) == g_] for g_ in ["A_KYC", "B_rule", "C_trx", "D_derived"]}
groups["C_popularity(leak ringan)"] = [c for c in FINAL if c in POPULARITY]
for gname, cols in groups.items():
    if not cols: continue
    rest = [c for c in FINAL if c not in cols]
    if len(rest) < 2: abl.append(dict(group=gname, n_dropped=len(cols), note="sisa fitur <2, skip")); continue
    m2 = mk(BEST).fit(DEV[rest], DEV.y); a_test = roc_auc_score(TEST.y, m2.predict_proba(TEST[rest])[:, 1])
    a_oot = np.mean(cv_run(rest, BEST)[0])
    abl.append(dict(group=gname, n_dropped=len(cols), dropped=";".join(cols), auc_test=a_test,
                    delta_test=a_test - auc_t, auc_oot=a_oot, delta_oot=a_oot - np.mean(oot_aucs)))
abl = pd.DataFrame(abl); print("\nABLATION (AUC turun jika kelompok dibuang):\n", abl.drop(columns=["dropped"], errors="ignore").round(4).to_string(index=False))
save(abl, "ablation_v3.csv")

# Reason codes
def reasons(model, d, feats, k=3):
    dm = xgb.DMatrix(d[feats].astype(float).values, feature_names=feats)
    contrib = model.get_booster().predict(dm, pred_contribs=True)[:, :-1]
    out = []
    for i in range(len(d)):
        top = np.argsort(-contrib[i])[:k]
        out.append("; ".join(f"{feats[j]}={'NA' if pd.isna(d.iloc[i][feats[j]]) else round(float(d.iloc[i][feats[j]]), 3)}"
                             f"({contrib[i, j]:+.2f})" for j in top if contrib[i, j] > 0))
    return out

# ----------------------------------------------------------------------------
# FASE 9 - Output
# ----------------------------------------------------------------------------
hdr("FASE 9 - OUTPUT")
ALL = df.copy()
ALL["score"] = final_model.predict_proba(ALL[FINAL])[:, 1]
ALL["tier"] = tier_of(ALL.score.values)
ALL["flag_recommended"] = ALL.tier.isin(["Tinggi", "Sedang"]).astype(int)
ALL["top_reasons"] = reasons(final_model, ALL, FINAL)
ALL["split"] = np.where(ALL.m == TEST_M, "test", np.where(ALL.m == VAL_M, "validate(in-sample)", "train(in-sample)"))
save(ALL[["key1", "accountid", "alert_month", "score", "tier", "flag_recommended", "top_reasons", "split"]],
     "scored_all_key1_v3.csv")
final_model.save_model(OUT + "xgb_production_v3.json"); print("[saved] xgb_production_v3.json")

card = dict(
    excluded_manually=EXCLUDE_MANUAL,
    purpose="Prioritisasi alert FDS (ranking/triage), BUKAN deteksi fraud absolut. Label = disposition analis (mewarisi bias analis).",
    data=dict(file=PATH, filter="flag=='FDS'", n_alerts=len(df), n_positive=int(df.y.sum()), base_rate=float(df.y.mean()),
              n_accounts=int(df.accountid.nunique()), months=sorted(df.m.unique().tolist()),
              split=dict(train=TRAIN_M, validate=VAL_M, test=TEST_M), skipped_whitelist_cols=skipped,
              rolling_origin_folds=[dict(train=a, test=b) for a, b in FOLDS]),
    features_final=FINAL, feature_groups={c: GROUP[c] for c in FINAL}, params=BEST,
    feature_selection=dict(method="stability selection (permutation importance OOT, banyak seed)", n_seeds=STAB_SEEDS, min_freq=STAB_MIN_FREQ,
                           auc_oot_set_stabil=a_st, auc_oot_pool_penuh=float(base_auc)),
    threshold=dict(strategy="tier_by_model_score_uncalibrated", tier_cuts=TIER_CUTS, tier_summary_test=tt_test.to_dict("records"),
                   f05_best_oot=best_f05.to_dict("records")),
    metrics_oot=dict(auc_folds=oot_aucs, auc_mean=float(np.mean(oot_aucs)), auc_std=float(np.std(oot_aucs))),
    metrics_test=dict(auc=auc_t, auc_ci95=ci(0).tolist(), pr_auc=pr_t, pr_auc_ci95=ci(1).tolist(), n_flag=n_flag,
                      precision=prec_t, precision_ci95=ci(3).tolist(), recall=rec_t, recall_ci95=ci(4).tolist(),
                      auc_new_accounts=auc_new, n_new_accounts=int(len(new))),
    ablation=abl.drop(columns=["dropped"], errors="ignore").to_dict("records"),
    limitations=[
        "Label adalah keputusan analis (bukan ground truth fraud); model meniru kebijakan/bias analis.",
        "Data kecil (824 alert, 6 bulan, test satu bulan) -> CI lebar; AUC test tidak boleh dibaca sebagai angka pasti.",
        "Fitur counterparty popularity dihitung dari snapshot bulanan: risiko leakage ringan (lihat ablation).",
        "Skor pada baris train/validate di scored_all adalah in-sample (optimistis); gunakan hanya baris split=test untuk evaluasi.",
        "Skor adalah skor ranking model, bukan probabilitas terkalibrasi (jangan dibaca sebagai peluang fraud). Tier memakai batas skor tetap; tingkat suspicious historis per tier hanya gambaran dengan rentang ketidakpastian lebar, dan batas ditinjau ulang setiap model dilatih ulang.",
        "Run ini adalah revisi (n_channel_l3m dibuang) setelah test 2026-06 pernah dilihat di run sebelumnya: angka test tidak lagi 100% murni; andalkan OOT untuk perbandingan.",
        "Perlu monitoring PSI dan refit berkala; drift >0.25 = tinjau ulang."])
json.dump(card, open(OUT + "model_card_v3.json", "w"), indent=2, default=py); print("[saved] model_card_v3.json")

hdr("RINGKASAN AKHIR")
print(f"Fitur final ({len(FINAL)}): {FINAL}")
print(f"AUC OOT   : {np.mean(oot_aucs):.4f} ± {np.std(oot_aucs):.4f}")
print(f"AUC test  : {auc_t:.4f}  CI95 [{ci(0)[0]:.4f}, {ci(0)[1]:.4f}]")
print(f"@ flag (tier Tinggi+Sedang): n_flag={n_flag} | precision={prec_t:.3f} | recall={rec_t:.3f}")
print("Tier (Test):", {r["tier"]: (r["n"], round(r["susp_rate"], 3)) for r in tt_test.to_dict("records")})
print("Ablation (delta AUC test):", {r["group"]: round(r.get("delta_test", np.nan), 4) for r in abl.to_dict("records")})
t5 = ALL[ALL.split == "test"].sort_values("score", ascending=False).head(5)
print("\nTop-5 alert (test) + reason code:")
print(t5[["key1", "alert_month", "score", "top_reasons"]].to_string(index=False))
