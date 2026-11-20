"""Dashboard data: the read-only queries behind the administrator and staff
dashboards. Nothing here writes to the database.

All "days" are local days (see TIMEZONE_OFFSET_MINUTES in ml/settings.py), and
the seeded SAMPLE DATA placeholder rows are excluded, as in analysis.py."""

import json
import os
import csv
from collections import Counter

from datetime import datetime, timedelta, timezone

from analysis import SAMPLE_MARKER
from ml import settings
from ml.explain import LEVELS

# Log actions that count as failures on the dashboard.
FAILURE_ACTIONS = ("login_failed", "access_denied", "csrf_failure")
WINDOW_DAYS = 14          # how many days the charts cover
RECENT_ACTIVITY = 10
RECENT_ALERTS = 5

# SQL condition that skips the seeded placeholder rows (takes SAMPLE_MARKER as its value).
REAL_ROWS = "COALESCE(details, '') <> ?"


def local_today():
    """Today's date in local time (the database itself stores UTC)."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return (now + timedelta(minutes=settings.TIMEZONE_OFFSET_MINUTES)).date()


def _offset():
    """The local-time offset in the form SQLite's date() function expects."""
    return f"{settings.TIMEZONE_OFFSET_MINUTES:+d} minutes"


def admin_dashboard(db, today=None, days=WINDOW_DAYS):
    """Everything the administrator dashboard shows: summary numbers, chart data,
    the role comparison, the highest-risk new alerts and the latest activity."""
    today = today or local_today()
    today_s = today.isoformat()
    start = today - timedelta(days=days - 1)
    start_s = start.isoformat()
    off = _offset()
    fail_marks = ",".join("?" for _ in FAILURE_ACTIONS)

    def one(sql, params=()):
        return db.execute(sql, params).fetchone()[0]

    today_sql = (f"SELECT COUNT(*) FROM activity_logs WHERE {REAL_ROWS} "
                 "AND date(timestamp, ?) = ?")
    today_args = (SAMPLE_MARKER, off, today_s)
    cards = {
        "active_users": one("SELECT COUNT(*) FROM users WHERE is_active = 1"),
        "patients": one("SELECT COUNT(*) FROM patients"),
        "medical_records": one("SELECT COUNT(*) FROM medical_records"),
        "events_today": one(today_sql, today_args),
        "failed_logins_today": one(today_sql + " AND action = 'login_failed'", today_args),
        "denied_today": one(today_sql + " AND action = 'access_denied'", today_args),
        "new_alerts": one("SELECT COUNT(*) FROM alerts WHERE status = 'new'"),
        "high_critical_new": one("SELECT COUNT(*) FROM alerts WHERE status = 'new' "
                                 "AND risk_level IN ('high', 'critical')"),
    }

    # ---- Events per day, and how many of them were failures ----
    by_day = {
        r["d"]: (r["n"], r["bad"])
        for r in db.execute(
            "SELECT date(timestamp, ?) AS d, COUNT(*) AS n, "
            f"SUM(CASE WHEN action IN ({fail_marks}) THEN 1 ELSE 0 END) AS bad "
            f"FROM activity_logs WHERE {REAL_ROWS} AND date(timestamp, ?) >= ? GROUP BY d",
            (off, *FAILURE_ACTIONS, SAMPLE_MARKER, off, start_s))
    }
    day_list = [(start + timedelta(days=i)).isoformat() for i in range(days)]

    # ---- Model results per day (flagged and normal user-days) ----
    analyzed = {
        r["result_date"]: (r["n"], r["flagged"] or 0)
        for r in db.execute(
            "SELECT result_date, COUNT(*) AS n, SUM(is_anomaly) AS flagged "
            "FROM analysis_results WHERE result_date >= ? GROUP BY result_date",
            (start_s,))
    }

    # ---- Alerts grouped by risk level and review status ----
    risk = {"new": {lv: 0 for lv in LEVELS}, "reviewed": {lv: 0 for lv in LEVELS}}
    for r in db.execute("SELECT risk_level, status, COUNT(*) AS n FROM alerts "
                        "GROUP BY risk_level, status"):
        risk[r["status"]][r["risk_level"]] = r["n"]

    chart = {
        "days": day_list,
        "events": [by_day.get(d, (0, 0))[0] for d in day_list],
        "failures": [by_day.get(d, (0, 0))[1] for d in day_list],
        "normal_days": [analyzed.get(d, (0, 0))[0] - analyzed.get(d, (0, 0))[1]
                        for d in day_list],
        "flagged_days": [analyzed.get(d, (0, 0))[1] for d in day_list],
        "levels": list(LEVELS),
        "alerts_new": [risk["new"][lv] for lv in LEVELS],
        "alerts_reviewed": [risk["reviewed"][lv] for lv in LEVELS],
    }

    # ---- Comparison across roles ----
    active = {r["role"]: r["n"] for r in db.execute(
        "SELECT role, COUNT(*) AS n FROM users WHERE is_active = 1 GROUP BY role")}
    role_events = {
        r["role"]: (r["n"], r["bad"])
        for r in db.execute(
            f"SELECT role, COUNT(*) AS n, "
            f"SUM(CASE WHEN action IN ({fail_marks}) THEN 1 ELSE 0 END) AS bad "
            f"FROM activity_logs WHERE {REAL_ROWS} AND role IS NOT NULL "
            "AND date(timestamp, ?) >= ? GROUP BY role",
            (*FAILURE_ACTIONS, SAMPLE_MARKER, off, start_s))
    }
    role_flagged = {r["role"]: r["n"] for r in db.execute(
        "SELECT role, COUNT(*) AS n FROM analysis_results "
        "WHERE is_anomaly = 1 AND result_date >= ? GROUP BY role", (start_s,))}
    roles = [{
        "role": role,
        "active_users": active.get(role, 0),
        "events": role_events.get(role, (0, 0))[0],
        "failures": role_events.get(role, (0, 0))[1],
        "flagged_days": role_flagged.get(role, 0),
    } for role in settings.ROLES]

    recent_activity = db.execute(
        "SELECT l.id, l.timestamp, COALESCE(u.username, l.username_attempted) AS actor, "
        "l.role, l.action, l.resource_type, l.resource_id, l.success "
        "FROM activity_logs l LEFT JOIN users u ON u.id = l.user_id "
        "WHERE COALESCE(l.details, '') <> ? ORDER BY l.id DESC LIMIT ?",
        (SAMPLE_MARKER, RECENT_ACTIVITY)).fetchall()

    recent_alerts = db.execute(
        "SELECT a.id, a.alert_date, a.risk_level, a.explanation, a.anomaly_score, "
        "u.username, u.role AS user_role FROM alerts a "
        "LEFT JOIN users u ON u.id = a.user_id WHERE a.status = 'new' "
        "ORDER BY CASE a.risk_level WHEN 'critical' THEN 4 WHEN 'high' THEN 3 "
        "WHEN 'medium' THEN 2 ELSE 1 END DESC, a.id DESC LIMIT ?",
        (RECENT_ALERTS,)).fetchall()

    return {
    "window_days": days,
    "cards": cards,
    "chart": chart,
    "roles": roles,
    "recent_activity": recent_activity,
    "recent_alerts": recent_alerts,
    "last_analysis": one("SELECT MAX(scored_at) FROM analysis_results"),
    "model_evaluation": get_model_evaluation(),
    "synthetic_dataset": get_synthetic_dataset_summary(),
}


def staff_dashboard(db, user_id, include_patients, include_records, today=None):
    """Dashboard for doctors, nurses and receptionists (and the admin fallback).
    Shows aggregate counts the role may see and the user's OWN recent activity."""
    today = today or local_today()

    def one(sql, params=()):
        return db.execute(sql, params).fetchone()[0]

    return {
        "patients": one("SELECT COUNT(*) FROM patients") if include_patients else None,
        "medical_records": (one("SELECT COUNT(*) FROM medical_records")
                            if include_records else None),
        "my_events_today": one(
            f"SELECT COUNT(*) FROM activity_logs WHERE user_id = ? AND {REAL_ROWS} "
            "AND date(timestamp, ?) = ?",
            (user_id, SAMPLE_MARKER, _offset(), today.isoformat())),
        "my_recent": db.execute(
            "SELECT id, timestamp, action, resource_type, resource_id, success "
            f"FROM activity_logs WHERE user_id = ? AND {REAL_ROWS} "
            "ORDER BY id DESC LIMIT 8", (user_id, SAMPLE_MARKER)).fetchall(),
    }

def get_model_evaluation():
    path = os.path.join(
        settings.REPORT_DIR,
        "evaluation_multiseed.json"
    )

    if not os.path.exists(path):
        return None

    with open(path, encoding="utf-8") as f:
        result = json.load(f)

    return {
        "runs": result["n_runs"],
        "precision": result["per_role_models"]["precision"]["mean"],
        "recall": result["per_role_models"]["recall"]["mean"],
        "f1": result["per_role_models"]["f1"]["mean"],
        "roc_auc": result["per_role_models"]["roc_auc"]["mean"],
        "baseline_f1": result["global_baseline"]["f1"]["mean"]
    }


def get_synthetic_dataset_summary():
    """Read summary statistics from the synthetic ML dataset."""

    meta_path = settings.META_PATH
    labels_path = settings.LABELS_PATH

    if not os.path.exists(meta_path) or not os.path.exists(labels_path):
        return None

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    roles = {
        role: {
            "users": count,
            "user_days": 0,
            "anomalies": 0
        }
        for role, count in meta["users_per_role"].items()
    }

    splits = {
        "train": {"user_days": 0, "anomalies": 0},
        "test": {"user_days": 0, "anomalies": 0}
    }

    scenarios = Counter()
    total_anomalies = 0
    total_user_days = 0

    with open(labels_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            role = row["role"]
            split = row["split"]
            anomaly = int(row["is_anomaly"])

            total_user_days += 1
            roles[role]["user_days"] += 1
            splits[split]["user_days"] += 1

            if anomaly:
                total_anomalies += 1
                roles[role]["anomalies"] += 1
                splits[split]["anomalies"] += 1
                scenarios[row["scenario"]] += 1

    return {
        "users": sum(meta["users_per_role"].values()),
        "events": meta["n_events"],
        "days": meta["n_days"],
        "user_days": total_user_days,
        "anomalies": total_anomalies,
        "normal_days": total_user_days - total_anomalies,
        "start_date": meta["start_date"],
        "split_date": meta["split_date"],
        "roles": [
            {"role": role, **values}
            for role, values in roles.items()
        ],
        "splits": splits,
        "scenarios": [
            {"name": name, "count": count}
            for name, count in scenarios.most_common()
        ]
    }
