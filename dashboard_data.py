"""Read-only queries behind the dashboards. Nothing here writes to the database.
All 'days' are local days (ml.settings.TIMEZONE_OFFSET_MINUTES), and the seeded
'SAMPLE DATA' placeholder rows are excluded, as in analysis.py."""
from datetime import datetime, timedelta, timezone

from analysis import SAMPLE_MARKER
from ml import settings
from ml.explain import LEVELS

FAILURE_ACTIONS = ("login_failed", "access_denied", "csrf_failure")
WINDOW_DAYS = 14
RECENT_ACTIVITY = 10
RECENT_ALERTS = 5

REAL_ROWS = "COALESCE(details, '') <> ?"


def local_today():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return (now + timedelta(minutes=settings.TIMEZONE_OFFSET_MINUTES)).date()


def _offset():
    return f"{settings.TIMEZONE_OFFSET_MINUTES:+d} minutes"


def admin_dashboard(db, today=None, days=WINDOW_DAYS):
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

    # ---- daily activity (events, and failed/denied events) ----
    by_day = {
        r["d"]: (r["n"], r["bad"])
        for r in db.execute(
            "SELECT date(timestamp, ?) AS d, COUNT(*) AS n, "
            f"SUM(CASE WHEN action IN ({fail_marks}) THEN 1 ELSE 0 END) AS bad "
            f"FROM activity_logs WHERE {REAL_ROWS} AND date(timestamp, ?) >= ? GROUP BY d",
            (off, *FAILURE_ACTIONS, SAMPLE_MARKER, off, start_s))
    }
    day_list = [(start + timedelta(days=i)).isoformat() for i in range(days)]

    # ---- model results per day ----
    analyzed = {
        r["result_date"]: (r["n"], r["flagged"] or 0)
        for r in db.execute(
            "SELECT result_date, COUNT(*) AS n, SUM(is_anomaly) AS flagged "
            "FROM analysis_results WHERE result_date >= ? GROUP BY result_date",
            (start_s,))
    }

    # ---- alerts by risk level and status ----
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

    # ---- comparison across roles ----
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