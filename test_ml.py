"""Part 4 verification: features, data generator, training, scoring, evaluation.
Uses temporary files only. Run:  python test_ml.py
(Takes a short while: it generates and trains on a small synthetic dataset.)
"""
import os
import shutil
import tempfile

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from ml import settings
from ml.detector import Detector
from ml.evaluate import label_features, run_evaluation
from ml.features import extract_features
from ml.generate_data import generate
from ml.train import fit_bundle, save_bundle

passed = 0
COLUMNS = ["user_id", "role", "action", "resource_id", "records_accessed", "success", "timestamp"]


def check(name, condition):
    global passed
    if not condition:
        raise AssertionError(f"FAILED: {name}")
    passed += 1
    print(f"  [PASS] {name}")


def ev(user_id, role, action, ts, rid=None, rec=0, ok=1):
    return (user_id, role, action, rid, rec, ok, ts)


def main():
    # ------------------------------------------------------------- features
    print("Feature extraction (hand-built day)")
    day = "2025-03-03"
    rows = [
        ev(1, "doctor", "login", f"{day} 09:05:00"),
        ev(1, "doctor", "view_patient", f"{day} 09:10:00", 5, 1),
        ev(1, "doctor", "view_patient", f"{day} 09:20:00", 5, 1),
        ev(1, "doctor", "view_patient", f"{day} 09:30:00", 6, 1),
        ev(1, "doctor", "search_patients", f"{day} 09:40:00", None, 3),
        ev(1, "doctor", "list_patients", f"{day} 09:45:00", None, 8),
        ev(1, "doctor", "update_patient", f"{day} 10:00:00", 5, 1),
        ev(1, "doctor", "create_medical_record", f"{day} 10:05:00", 5, 0),
        ev(1, "doctor", "access_denied", f"{day} 10:10:00", None, 0, 0),
        ev(1, "doctor", "login_failed", f"{day} 10:15:00", None, 0, 0),
        ev(1, "doctor", "view_patient", f"{day} 23:10:00", 7, 1),
        ev(2, "nurse", "login_failed", f"{day} 03:00:00", None, 0, 0),
        ev(None, None, "login_failed", f"{day} 04:00:00", None, 0, 0),
    ]
    f = extract_features(pd.DataFrame(rows, columns=COLUMNS), tz_offset_minutes=0)
    check("rows without a user are ignored (2 user-days)", len(f) == 2)
    d = f[f["user_id"] == 1].iloc[0]
    check("total_events = 11", d["total_events"] == 11)
    check("login_hour = 9", d["login_hour"] == 9)
    check("off_hours_actions = 1 (the 23:10 view)", d["off_hours_actions"] == 1)
    check("failed_logins = 1", d["failed_logins"] == 1)
    check("records_accessed = 16", d["records_accessed"] == 16)
    check("patients_viewed = 3 distinct patients", d["patients_viewed"] == 3)
    check("list_search_actions = 2", d["list_search_actions"] == 2)
    check("write_actions = 2", d["write_actions"] == 2)
    check("denied_actions = 1", d["denied_actions"] == 1)
    check("peak_hourly_events = 6", d["peak_hourly_events"] == 6)
    n = f[f["user_id"] == 2].iloc[0]
    check("day without a login uses first event hour", n["login_hour"] == 3)
    check("night failed login counts as off-hours", n["off_hours_actions"] == 1 and n["failed_logins"] == 1)

    one = pd.DataFrame([ev(3, "doctor", "login", "2025-03-01 20:00:00")], columns=COLUMNS)
    g = extract_features(one, tz_offset_minutes=330).iloc[0]
    check("UTC converted to local date (+05:30 crosses midnight)", g["date"] == "2025-03-02")
    check("converted hour is 1 and off-hours", g["login_hour"] == 1 and g["off_hours_actions"] == 1)
    check("empty input returns an empty feature table",
          len(extract_features(pd.DataFrame(rows, columns=COLUMNS).iloc[0:0])) == 0)
    try:
        extract_features(pd.DataFrame({"user_id": [1]}))
        check("missing columns raise ValueError", False)
    except ValueError:
        check("missing columns raise ValueError", True)

    # ------------------------------------------------------------ generator
    print("Synthetic data generator")
    e1, l1, m1 = generate(seed=11, n_days=40, train_days=28)
    e2, l2, _ = generate(seed=11, n_days=40, train_days=28)
    e3, _, _ = generate(seed=12, n_days=40, train_days=28)
    check("same seed gives identical data", e1.equals(e2) and l1.equals(l2))
    check("different seed gives different data", not e1.equals(e3))
    check("all four roles are present", set(l1["role"]) == set(settings.ROLES))
    check("both normal and anomalous days exist", set(l1["is_anomaly"]) == {0, 1})
    check("train and test splits exist", set(l1["split"]) == {"train", "test"})
    check("admins never view patients",
          not ((e1["role"] == "admin") & (e1["action"] == "view_patient")).any())
    feats = extract_features(e1)
    check("one feature row per labelled user-day", len(feats) == len(l1))
    label_features(feats, l1)
    check("every feature row matches exactly one label", True)

    # ------------------------------------------------------------- training
    print("Training and scoring (small synthetic dataset, takes a moment)")
    events, labels, _ = generate(seed=7, n_days=90, train_days=60)
    labeled = label_features(extract_features(events), labels)
    train = labeled[labeled["split"] == "train"]
    bundle = fit_bundle(train)
    det = Detector(bundle)
    check("one model per role", set(bundle["models"]) == set(settings.ROLES))
    check("models are scikit-learn IsolationForest",
          all(isinstance(m, IsolationForest) for m in bundle["models"].values()))
    check("thresholds come from training scores only",
          all(abs(det.thresholds[r] - np.quantile(det.train_scores[r], 1 - bundle["flag_rate"])) < 1e-12
              for r in settings.ROLES))

    sample = labeled[labeled["role"] == "nurse"].head(20)
    direct = -bundle["models"]["nurse"].score_samples(sample[settings.FEATURE_COLUMNS])
    via = det.score(sample)["anomaly_score"].to_numpy()
    check("detector scores equal the model's own scores", np.allclose(direct, via))

    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "model.joblib")
    save_bundle(bundle, path)
    reloaded = Detector.load(path)
    check("saved and reloaded model gives identical scores",
          np.allclose(reloaded.score(sample)["anomaly_score"], det.score(sample)["anomaly_score"]))
    shutil.rmtree(tmp, ignore_errors=True)

    try:
        det.score(pd.DataFrame([{"role": "hacker", **{c: 0.0 for c in settings.FEATURE_COLUMNS}}]))
        check("unknown role raises ValueError", False)
    except ValueError:
        check("unknown role raises ValueError", True)

    # ----------------------------------------------- normal vs anomalous cases
    print("Normal and anomalous examples")

    def example(role, **overrides):
        row = {c: det.baselines[role][c]["median"] for c in settings.FEATURE_COLUMNS}
        row.update(overrides)
        row.update({"user_id": 0, "role": role, "date": "2025-01-01"})
        return row

    cases = {
        "normal doctor (role median)": example("doctor"),
        "doctor bulk access": example("doctor", records_accessed=1500, patients_viewed=200,
                                      list_search_actions=60, total_events=400,
                                      peak_hourly_events=150),
        "nurse at 2am": example("nurse", login_hour=2, off_hours_actions=45, total_events=60),
        "receptionist credential attack": example("receptionist", failed_logins=25,
                                                  total_events=40, peak_hourly_events=25),
        "nurse probing restricted pages": example(
            "nurse",
            denied_actions=15,
            total_events=52,
            peak_hourly_events=15,
        ),
        "doctor mass modification": example("doctor", write_actions=50, total_events=100),
    }
    out = det.score(pd.DataFrame(list(cases.values())))
    normal_score = out.iloc[0]["anomaly_score"]
    check("normal (median) doctor day is not flagged", not bool(out.iloc[0]["is_anomaly"]))
    for i, name in enumerate(list(cases)[1:], start=1):
        check(f"{name}: flagged", bool(out.iloc[i]["is_anomaly"]))
        check(f"{name}: scores higher than the normal day", out.iloc[i]["anomaly_score"] > normal_score)

    # ----------------------------------------------------------- evaluation
    print("Evaluation (sanity floors, not performance claims)")
    res = run_evaluation(events, labels, det)
    o = res["overall"]
    check("confusion matrix covers every test row",
          o["tp"] + o["fp"] + o["fn"] + o["tn"] == res["dataset"]["test_rows"])
    check("test period contains anomalies", o["tp"] + o["fn"] == res["dataset"]["test_anomalous_days"] > 0)
    check("pooled ROC-AUC is at least 0.85", o["roc_auc_pooled_margin"] >= 0.85)
    check("recall is at least 0.5", o["recall"] >= 0.5)
    check("false-positive rate is at most 0.15", o["fpr"] <= 0.15)
    check("per-role and per-scenario results are produced",
          set(res["by_role"]) == set(settings.ROLES) and len(res["by_scenario"]) >= 3)
    check("global baseline and sensitivity table are produced",
          res["global_baseline_no_roles"]["roc_auc"] is not None
          and len(res["threshold_sensitivity"]) == 5)
    print(f"  (info) precision={o['precision']:.3f} recall={o['recall']:.3f} "
          f"f1={o['f1']:.3f} fpr={o['fpr']:.3f} auc={o['roc_auc_pooled_margin']:.3f}")

    print(f"\nAll {passed} checks passed.")


if __name__ == "__main__":
    main()