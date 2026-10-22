"""Evaluates the trained detector on the held-out TEST period using labels.
All numbers are computed here; nothing is hard-coded.

Run:  python -m ml.evaluate
"""
import json
import os

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import average_precision_score, roc_auc_score

from ml import settings
from ml.detector import Detector
from ml.features import extract_features

SENSITIVITY_RATES = [0.02, 0.03, 0.05, 0.08, 0.10]


# ------------------------------------------------------------------- metrics
def binary_metrics(actual, flagged):
    actual = np.asarray(actual).astype(int)
    flagged = np.asarray(flagged).astype(int)
    tp = int(((actual == 1) & (flagged == 1)).sum())
    fp = int(((actual == 0) & (flagged == 1)).sum())
    fn = int(((actual == 1) & (flagged == 0)).sum())
    tn = int(((actual == 0) & (flagged == 0)).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": precision,
            "recall": recall, "f1": f1, "fpr": fpr}


def safe_auc(actual, scores):
    if len(set(np.asarray(actual).tolist())) < 2:
        return None
    return float(roc_auc_score(actual, scores))


def safe_ap(actual, scores):
    if len(set(np.asarray(actual).tolist())) < 2:
        return None
    return float(average_precision_score(actual, scores))


def label_features(features, labels):
    cols = ["user_id", "date", "split", "is_anomaly", "scenario"]
    merged = features.merge(labels[cols], on=["user_id", "date"], how="left",
                            validate="one_to_one")
    if merged["is_anomaly"].isna().any():
        raise ValueError("Some feature rows have no matching label.")
    merged = merged.rename(columns={"is_anomaly": "actual"})
    merged["actual"] = merged["actual"].astype(int)
    return merged


# ---------------------------------------------------------------- evaluation
def run_evaluation(events, labels, detector):
    labeled = label_features(extract_features(events), labels)
    train = labeled[labeled["split"] == "train"].reset_index(drop=True)
    test = labeled[labeled["split"] == "test"].reset_index(drop=True)
    if test.empty or test["actual"].sum() == 0:
        raise ValueError("The test split needs at least one anomalous day.")

    scored = detector.score(test).rename(columns={"is_anomaly": "flagged"})
    scored["flagged"] = scored["flagged"].astype(int)
    # Scores of different role models are not on one scale, so pooled ranking
    # metrics use the margin: score minus that role's own threshold.
    scored["margin"] = scored["anomaly_score"] - scored["threshold"]

    overall = binary_metrics(scored["actual"], scored["flagged"])
    overall["roc_auc_pooled_margin"] = safe_auc(scored["actual"], scored["margin"])
    overall["average_precision_pooled_margin"] = safe_ap(scored["actual"], scored["margin"])

    by_role = {}
    for role in settings.ROLES:
        part = scored[scored["role"] == role]
        if part.empty:
            continue
        m = binary_metrics(part["actual"], part["flagged"])
        m["n_rows"] = int(len(part))
        m["n_anomalies"] = int(part["actual"].sum())
        m["roc_auc"] = safe_auc(part["actual"], part["anomaly_score"])
        by_role[role] = m

    by_scenario = {}
    for name, part in scored[scored["actual"] == 1].groupby("scenario"):
        by_scenario[str(name)] = {"n": int(len(part)), "detected": int(part["flagged"].sum()),
                                  "recall": float(part["flagged"].mean())}

    # Baseline for comparison: ONE model for all roles, no role information.
    gmodel = IsolationForest(n_estimators=detector.bundle["n_estimators"],
                             contamination="auto", random_state=detector.bundle["seed"])
    gmodel.fit(train[settings.FEATURE_COLUMNS])
    g_train = -gmodel.score_samples(train[settings.FEATURE_COLUMNS])
    g_threshold = float(np.quantile(g_train, 1 - detector.bundle["flag_rate"]))
    g_scores = -gmodel.score_samples(test[settings.FEATURE_COLUMNS])
    global_baseline = binary_metrics(test["actual"], (g_scores > g_threshold).astype(int))
    global_baseline["roc_auc"] = safe_auc(test["actual"], g_scores)

    # How the result changes with the chosen flag rate. Thresholds still come
    # from TRAINING scores only. Do not pick a rate by looking at this table.
    sensitivity = []
    for rate in SENSITIVITY_RATES:
        thr = {r: float(np.quantile(detector.train_scores[r], 1 - rate))
               for r in detector.models}
        flagged = (scored["anomaly_score"] > scored["role"].map(thr)).astype(int)
        m = binary_metrics(scored["actual"], flagged)
        m["flag_rate"] = rate
        sensitivity.append(m)

    profiles = train.groupby("role")[settings.FEATURE_COLUMNS].median()
    return {
        "dataset": {
            "train_rows": int(len(train)), "test_rows": int(len(test)),
            "train_anomalous_days": int(train["actual"].sum()),
            "test_anomalous_days": int(test["actual"].sum()),
            "test_prevalence": float(test["actual"].mean()),
            "flag_rate_used": detector.bundle["flag_rate"],
        },
        "overall": overall,
        "by_role": by_role,
        "by_scenario": by_scenario,
        "global_baseline_no_roles": global_baseline,
        "threshold_sensitivity": sensitivity,
        "role_profiles_median_train": profiles.to_dict(orient="index"),
    }


# ------------------------------------------------------------------ reporting
def fmt(x):
    return "n/a" if x is None else f"{x:.3f}"


def md_table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def render_markdown(res):
    d, o = res["dataset"], res["overall"]
    g = res["global_baseline_no_roles"]
    out = ["# MediShield AI: Isolation Forest evaluation", "",
           "Generated by `python -m ml.evaluate` from synthetic data. "
           "A flagged day means 'worth investigating', not proof of misuse.", "",
           "## Dataset", "",
           f"- Train: {d['train_rows']} user-days ({d['train_anomalous_days']} unlabelled anomalous)",
           f"- Test: {d['test_rows']} user-days ({d['test_anomalous_days']} anomalous, "
           f"prevalence {d['test_prevalence']:.1%}; inflated for evaluation)",
           f"- Threshold: {1 - d['flag_rate_used']:.0%} quantile of each role's training scores", "",
           "## Overall (per-role models, test period)", "",
           md_table(["TP", "FP", "FN", "TN", "Precision", "Recall", "F1", "FPR",
                     "ROC-AUC (margin)", "Avg precision (margin)"],
                    [[o["tp"], o["fp"], o["fn"], o["tn"], fmt(o["precision"]), fmt(o["recall"]),
                      fmt(o["f1"]), fmt(o["fpr"]), fmt(o["roc_auc_pooled_margin"]),
                      fmt(o["average_precision_pooled_margin"])]]), "",
           "## By role", "",
           md_table(["Role", "Rows", "Anomalies", "Precision", "Recall", "F1", "FPR", "ROC-AUC"],
                    [[r, m["n_rows"], m["n_anomalies"], fmt(m["precision"]), fmt(m["recall"]),
                      fmt(m["f1"]), fmt(m["fpr"]), fmt(m["roc_auc"])]
                     for r, m in res["by_role"].items()]), "",
           "## Recall by scenario", "",
           md_table(["Scenario", "Anomalous days", "Detected", "Recall"],
                    [[s, m["n"], m["detected"], fmt(m["recall"])]
                     for s, m in res["by_scenario"].items()]), "",
           "## Baseline: one global model, no role information", "",
           md_table(["Precision", "Recall", "F1", "FPR", "ROC-AUC"],
                    [[fmt(g["precision"]), fmt(g["recall"]), fmt(g["f1"]), fmt(g["fpr"]),
                      fmt(g["roc_auc"])]]), "",
           "## Threshold sensitivity (informational; do not tune on test data)", "",
           md_table(["Flag rate", "Precision", "Recall", "F1", "FPR"],
                    [[f"{m['flag_rate']:.0%}", fmt(m["precision"]), fmt(m["recall"]),
                      fmt(m["f1"]), fmt(m["fpr"])] for m in res["threshold_sensitivity"]]), ""]
    return "\n".join(out)


def print_report(res):
    d, o = res["dataset"], res["overall"]
    print("DATASET")
    print(f"  train rows: {d['train_rows']} ({d['train_anomalous_days']} anomalous, unlabelled in training)")
    print(f"  test rows : {d['test_rows']} ({d['test_anomalous_days']} anomalous, prevalence {d['test_prevalence']:.1%})")
    print(f"  flag rate used for thresholds: {d['flag_rate_used']:.0%}")
    print("\nOVERALL (per-role models, test period)")
    print(f"  TP={o['tp']} FP={o['fp']} FN={o['fn']} TN={o['tn']}")
    print(f"  precision={fmt(o['precision'])} recall={fmt(o['recall'])} f1={fmt(o['f1'])} "
          f"fpr={fmt(o['fpr'])}")
    print(f"  roc_auc(margin)={fmt(o['roc_auc_pooled_margin'])} "
          f"avg_precision(margin)={fmt(o['average_precision_pooled_margin'])}")
    print("\nBY ROLE")
    for role, m in res["by_role"].items():
        print(f"  {role:<13} rows={m['n_rows']:<4} anomalies={m['n_anomalies']:<3} "
              f"precision={fmt(m['precision'])} recall={fmt(m['recall'])} "
              f"fpr={fmt(m['fpr'])} auc={fmt(m['roc_auc'])}")
    print("\nRECALL BY SCENARIO")
    for name, m in res["by_scenario"].items():
        print(f"  {name:<20} {m['detected']}/{m['n']}  recall={fmt(m['recall'])}")
    g = res["global_baseline_no_roles"]
    print("\nBASELINE: one global model, no roles")
    print(f"  precision={fmt(g['precision'])} recall={fmt(g['recall'])} f1={fmt(g['f1'])} "
          f"fpr={fmt(g['fpr'])} auc={fmt(g['roc_auc'])}")
    print("\nTHRESHOLD SENSITIVITY (informational)")
    for m in res["threshold_sensitivity"]:
        print(f"  flag rate {m['flag_rate']:.0%}: precision={fmt(m['precision'])} "
              f"recall={fmt(m['recall'])} f1={fmt(m['f1'])} fpr={fmt(m['fpr'])}")


def _json_default(o):
    if isinstance(o, np.generic):
        return o.item()
    raise TypeError(f"Not JSON serializable: {type(o)}")


def write_reports(res):
    os.makedirs(settings.REPORT_DIR, exist_ok=True)
    with open(os.path.join(settings.REPORT_DIR, "evaluation.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2, default=_json_default)
    with open(os.path.join(settings.REPORT_DIR, "evaluation.md"), "w", encoding="utf-8") as f:
        f.write(render_markdown(res))


def main():
    for p in (settings.EVENTS_PATH, settings.LABELS_PATH):
        if not os.path.exists(p):
            raise FileNotFoundError(f"{p} not found. Run: python -m ml.generate_data")
    events = pd.read_csv(settings.EVENTS_PATH)
    labels = pd.read_csv(settings.LABELS_PATH)
    detector = Detector.load()
    res = run_evaluation(events, labels, detector)
    print_report(res)
    write_reports(res)
    print(f"\nReports written to {settings.REPORT_DIR}")


if __name__ == "__main__":
    main()