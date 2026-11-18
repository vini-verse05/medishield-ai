"""Trains one Isolation Forest per role on the TRAINING period only.
Training is unsupervised: labels are never used.

Run:  python -m ml.train
"""
import argparse
import json
import os
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import IsolationForest

from ml import settings
from ml.features import extract_features


def fit_bundle(train_features, seed=settings.RANDOM_SEED,
               n_estimators=settings.N_ESTIMATORS, flag_rate=settings.TARGET_FLAG_RATE):
    """train_features: DataFrame with a 'role' column and the feature columns."""
    models, thresholds, baselines, train_scores, n_rows = {}, {}, {}, {}, {}

    for role in settings.ROLES:
        X = train_features.loc[train_features["role"] == role, settings.FEATURE_COLUMNS]
        if len(X) < settings.MIN_TRAIN_ROWS:
            raise ValueError(
                f"Not enough training rows for role '{role}': "
                f"{len(X)} (need at least {settings.MIN_TRAIN_ROWS}).")

        model = IsolationForest(n_estimators=n_estimators, contamination="auto",
                                random_state=seed)
        model.fit(X)

        # sklearn's score_samples is "higher = more normal"; we negate it so
        # that a higher anomaly_score means more unusual.
        scores = -model.score_samples(X)
        models[role] = model
        thresholds[role] = float(np.quantile(scores, 1 - flag_rate))
        train_scores[role] = [float(s) for s in scores]
        n_rows[role] = int(len(X))
        baselines[role] = {
            c: {"median": float(X[c].median()),
                "p95": float(X[c].quantile(0.95)),
                "std": float(X[c].std(ddof=0))}
            for c in settings.FEATURE_COLUMNS
        }

    return {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sklearn_version": sklearn.__version__,
        "feature_columns": list(settings.FEATURE_COLUMNS),
        "flag_rate": flag_rate,
        "seed": seed,
        "n_estimators": n_estimators,
        "timezone_offset_minutes": settings.TIMEZONE_OFFSET_MINUTES,
        "models": models,
        "thresholds": thresholds,
        "baselines": baselines,    # each role's usual values, used to explain alerts
        "train_scores": train_scores,
        "n_train_rows": n_rows,
    }


def save_bundle(bundle, path=settings.MODEL_PATH):
    """Write the trained models to disk."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    joblib.dump(bundle, path)


def train_from_files(events_path=settings.EVENTS_PATH, meta_path=settings.META_PATH,
                     model_path=settings.MODEL_PATH):
    for p in (events_path, meta_path):
        if not os.path.exists(p):
            raise FileNotFoundError(f"{p} not found. Run: python -m ml.generate_data")
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    features = extract_features(pd.read_csv(events_path))
    train = features[features["date"] < meta["split_date"]]
    bundle = fit_bundle(train)
    save_bundle(bundle, model_path)
    return bundle, meta


def main():
    parser = argparse.ArgumentParser(description="Train the per-role Isolation Forests.")
    parser.add_argument("--events", default=settings.EVENTS_PATH)
    parser.add_argument("--meta", default=settings.META_PATH)
    parser.add_argument("--out", default=settings.MODEL_PATH)
    args = parser.parse_args()

    bundle, meta = train_from_files(args.events, args.meta, args.out)
    print(f"Trained on days before {meta['split_date']}.")
    print(f"{'role':<14}{'train rows':>11}{'threshold':>12}{'flagged in train':>18}")
    for role in settings.ROLES:
        scores = np.array(bundle["train_scores"][role])
        flagged = int((scores > bundle["thresholds"][role]).sum())
        print(f"{role:<14}{bundle['n_train_rows'][role]:>11}"
              f"{bundle['thresholds'][role]:>12.4f}{flagged:>18}")
    print(f"Model saved to {args.out}")


if __name__ == "__main__":
    main()