"""Loads the trained models and scores feature rows. Part 5 will call this."""
import os

import joblib
import numpy as np

from ml import settings


class Detector:
    def __init__(self, bundle):
        self.bundle = bundle
        self.models = bundle["models"]
        self.thresholds = bundle["thresholds"]
        self.baselines = bundle["baselines"]
        self.train_scores = bundle["train_scores"]
        self.feature_columns = bundle["feature_columns"]

    @classmethod
    def load(cls, path=settings.MODEL_PATH):
        if not os.path.exists(path):
            raise FileNotFoundError(f"{path} not found. Run: python -m ml.train")
        return cls(joblib.load(path))

    def score(self, features):
        """features: DataFrame with 'role' and the feature columns.
        Returns a copy with anomaly_score, threshold and is_anomaly added.
        anomaly_score: higher = more unusual for that role. is_anomaly means
        'worth investigating', not 'malicious'."""
        result = features.copy().reset_index(drop=True)
        result["anomaly_score"] = np.nan
        result["threshold"] = np.nan

        for role in result["role"].unique():
            if role not in self.models:
                raise ValueError(f"No trained model for role '{role}'.")
            mask = result["role"] == role
            X = result.loc[mask, self.feature_columns]
            result.loc[mask, "anomaly_score"] = -self.models[role].score_samples(X)
            result.loc[mask, "threshold"] = self.thresholds[role]

        result["is_anomaly"] = result["anomaly_score"] > result["threshold"]
        return result