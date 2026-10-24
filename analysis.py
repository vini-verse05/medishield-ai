"""Connects the application's activity_logs to the Part 4 ML pipeline:
logs -> features -> Isolation Forest score -> stored result -> alert.
Run from the web (admin button) or with:  flask --app app analyze"""
import json
from datetime import datetime

import click
import pandas as pd
from flask import current_app
from flask.cli import with_appcontext

from database import get_db
from ml import settings
from ml.detector import Detector
from ml.explain import build_explanation, build_signals, determine_risk
from ml.features import REQUIRED_COLUMNS, extract_features

# The fictional rows inserted by seed_data.py carry this marker; they are
# placeholders, not user behaviour, so they are never analysed.
SAMPLE_MARKER = "SAMPLE DATA"


def get_detector():
    """Tests may inject a detector via app.config['ML_DETECTOR']."""
    injected = current_app.config.get("ML_DETECTOR")
    if injected is not None:
        return injected
    return Detector.load(current_app.config.get("ML_MODEL_PATH", settings.MODEL_PATH))


def load_events(db):
    rows = db.execute(
        "SELECT " + ", ".join(REQUIRED_COLUMNS) + " FROM activity_logs "
        "WHERE COALESCE(details, '') <> ? ORDER BY id",
        (SAMPLE_MARKER,),
    ).fetchall()
    return pd.DataFrame([tuple(r) for r in rows], columns=REQUIRED_COLUMNS)


def latest_log_id(db, user_id, date_str):
    """Most recent log row of that user on that local date."""
    offset = f"{settings.TIMEZONE_OFFSET_MINUTES:+d} minutes"
    row = db.execute(
        "SELECT MAX(id) FROM activity_logs WHERE user_id = ? "
        "AND date(timestamp, ?) = ? AND COALESCE(details, '') <> ?",
        (user_id, offset, date_str, SAMPLE_MARKER),
    ).fetchone()
    return row[0]


def run_analysis(db, detector, since_date=None):
    """Score every user-day in activity_logs, store all results, and create or
    update one alert per flagged user-day. since_date: 'YYYY-MM-DD' (local)."""
    summary = {"days_analyzed": 0, "flagged": 0, "alerts_created": 0,
               "alerts_updated": 0, "alerts_skipped_reviewed": 0}

    features = extract_features(load_events(db))
    if since_date:
        features = features[features["date"] >= since_date]
    if features.empty:
        return summary

    scored = detector.score(features.reset_index(drop=True))

    for row in scored.to_dict("records"):
        user_id, role, day = int(row["user_id"]), row["role"], row["date"]
        values = {c: float(row[c]) for c in settings.FEATURE_COLUMNS}
        score, threshold = float(row["anomaly_score"]), float(row["threshold"])
        flagged = bool(row["is_anomaly"])

        db.execute(
            "INSERT INTO analysis_results (user_id, role, result_date, anomaly_score, "
            "threshold, is_anomaly, features_json, scored_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(user_id, result_date) DO UPDATE SET role = excluded.role, "
            "anomaly_score = excluded.anomaly_score, threshold = excluded.threshold, "
            "is_anomaly = excluded.is_anomaly, features_json = excluded.features_json, "
            "scored_at = excluded.scored_at",
            (user_id, role, day, score, threshold, 1 if flagged else 0, json.dumps(values)),
        )
        summary["days_analyzed"] += 1
        if not flagged:
            continue
        summary["flagged"] += 1

        baseline = detector.baselines[role]
        reference_max = max(detector.train_scores[role])
        risk = determine_risk(score, threshold, reference_max, values, baseline)
        signals = build_signals(values, baseline)
        explanation = build_explanation(
            role, signals, risk["escalation_reasons"] if risk["escalated"] else [])
        log_id = latest_log_id(db, user_id, day)

        existing = db.execute(
            "SELECT id, status FROM alerts WHERE user_id = ? AND alert_date = ?",
            (user_id, day),
        ).fetchone()
        if existing is None:
            db.execute(
                "INSERT INTO alerts (activity_log_id, user_id, anomaly_score, risk_level, "
                "explanation, alert_date, threshold, signals_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (log_id, user_id, score, risk["risk_level"], explanation, day,
                 threshold, json.dumps(signals)),
            )
            summary["alerts_created"] += 1
        elif existing["status"] == "new":
            db.execute(
                "UPDATE alerts SET activity_log_id = ?, anomaly_score = ?, risk_level = ?, "
                "explanation = ?, threshold = ?, signals_json = ? WHERE id = ?",
                (log_id, score, risk["risk_level"], explanation, threshold,
                 json.dumps(signals), existing["id"]),
            )
            summary["alerts_updated"] += 1
        else:  # already reviewed: never reopened or overwritten
            summary["alerts_skipped_reviewed"] += 1

    db.commit()
    return summary


def summary_text(s):
    return (f"Analyzed {s['days_analyzed']} user-days; {s['flagged']} flagged. "
            f"Alerts: {s['alerts_created']} new, {s['alerts_updated']} updated, "
            f"{s['alerts_skipped_reviewed']} left unchanged (already reviewed).")


@click.command("analyze")
@click.option("--since", default=None, help="Only analyze local dates on/after YYYY-MM-DD.")
@with_appcontext
def analyze_command(since):
    """Run the anomaly detector over the activity logs and create alerts."""
    if since:
        try:
            datetime.strptime(since, "%Y-%m-%d")
        except ValueError:
            raise click.ClickException("--since must look like 2026-10-24")
    try:
        detector = get_detector()
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc))
    click.echo(summary_text(run_analysis(get_db(), detector, since)))


def init_app(app):
    app.cli.add_command(analyze_command)