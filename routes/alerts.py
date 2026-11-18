"""Alert pages for administrators: list, detail, mark as reviewed, run analysis."""
import json

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)

import analysis
from audit import log_activity
from database import get_db
from ml import settings
from ml.explain import FEATURE_LABELS, LEVELS
from security import permission_required

bp = Blueprint("alerts", __name__, url_prefix="/admin/alerts")

MAX_ROWS = 200    # alerts shown on the list page
MAX_EVENTS = 100  # log rows shown on one alert's page


@bp.route("")
@permission_required("view_alerts")
def list_alerts():
    """All alerts, highest risk first, with filters for status and risk level."""
    db = get_db()
    status = request.args.get("status", "new")
    if status not in ("new", "reviewed", "all"):
        status = "new"
    risk = request.args.get("risk", "")
    if risk not in LEVELS:
        risk = ""

    where, params = [], []
    if status != "all":
        where.append("a.status = ?")
        params.append(status)
    if risk:
        where.append("a.risk_level = ?")
        params.append(risk)
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    rows = db.execute(
        "SELECT a.id, a.alert_date, a.anomaly_score, a.threshold, a.risk_level, "
        "a.explanation, a.status, a.created_at, a.reviewed_at, "
        "u.username, u.full_name, u.role AS user_role, r.username AS reviewer "
        "FROM alerts a LEFT JOIN users u ON u.id = a.user_id "
        "LEFT JOIN users r ON r.id = a.reviewed_by " + clause +
        " ORDER BY CASE a.risk_level WHEN 'critical' THEN 4 WHEN 'high' THEN 3 "
        "WHEN 'medium' THEN 2 ELSE 1 END DESC, a.created_at DESC, a.id DESC LIMIT ?",
        params + [MAX_ROWS],
    ).fetchall()

    new_counts = {level: 0 for level in LEVELS}
    for r in db.execute("SELECT risk_level, COUNT(*) AS n FROM alerts "
                        "WHERE status = 'new' GROUP BY risk_level"):
        new_counts[r["risk_level"]] = r["n"]

    log_activity("view_alerts", resource_type="alert",
                 details=f"status={status}; risk={risk or 'any'}; results={len(rows)}")
    return render_template("admin_alerts.html", alerts=rows, status=status, risk=risk,
                           levels=LEVELS, new_counts=new_counts, max_rows=MAX_ROWS)


@bp.route("/<int:alert_id>")
@permission_required("view_alerts")
def alert_detail(alert_id):
    """One alert with its explanation, the day's behaviour measures and log rows."""
    db = get_db()
    alert = db.execute(
        "SELECT a.*, u.username, u.full_name, u.role AS user_role, "
        "r.username AS reviewer FROM alerts a "
        "LEFT JOIN users u ON u.id = a.user_id "
        "LEFT JOIN users r ON r.id = a.reviewed_by WHERE a.id = ?",
        (alert_id,),
    ).fetchone()
    if alert is None:
        abort(404)

    signals = json.loads(alert["signals_json"]) if alert["signals_json"] else []
    features, events = {}, []
    if alert["alert_date"] and alert["user_id"] is not None:
        result = db.execute(
            "SELECT features_json FROM analysis_results "
            "WHERE user_id = ? AND result_date = ?",
            (alert["user_id"], alert["alert_date"]),
        ).fetchone()
        if result:
            features = json.loads(result["features_json"])
        offset = f"{settings.TIMEZONE_OFFSET_MINUTES:+d} minutes"
        events = db.execute(
            "SELECT id, timestamp, action, resource_type, resource_id, "
            "records_accessed, success FROM activity_logs "
            "WHERE user_id = ? AND date(timestamp, ?) = ? ORDER BY id DESC LIMIT ?",
            (alert["user_id"], offset, alert["alert_date"], MAX_EVENTS),
        ).fetchall()

    log_activity("view_alert", resource_type="alert", resource_id=alert_id)
    return render_template("admin_alert_detail.html", alert=alert, signals=signals,
                           features=features, events=events, labels=FEATURE_LABELS,
                           max_events=MAX_EVENTS)


@bp.route("/<int:alert_id>/review", methods=["POST"])
@permission_required("review_alerts")
def review(alert_id):
    """Mark an alert as reviewed, recording who did it and when."""
    db = get_db()
    alert = db.execute(
        "SELECT id, status, risk_level, user_id FROM alerts WHERE id = ?", (alert_id,)
    ).fetchone()
    if alert is None:
        abort(404)

    if alert["status"] == "reviewed":
        flash("This alert was already reviewed.", "info")
    else:
        db.execute(
            "UPDATE alerts SET status = 'reviewed', reviewed_by = ?, "
            "reviewed_at = datetime('now') WHERE id = ? AND status = 'new'",
            (g.user["id"], alert_id),
        )
        log_activity("review_alert", resource_type="alert", resource_id=alert_id,
                     details=f"risk={alert['risk_level']}; flagged_user_id={alert['user_id']}")
        flash("Alert marked as reviewed.", "success")
    return redirect(url_for("alerts.alert_detail", alert_id=alert_id))


@bp.route("/run", methods=["POST"])
@permission_required("review_alerts")
def run():
    """Run the anomaly analysis now (the "Run analysis now" button)."""
    try:
        detector = analysis.get_detector()
    except FileNotFoundError:
        flash("The trained model could not be found. Run "
              "'python -m ml.generate_data' and then 'python -m ml.train'.", "danger")
        return redirect(url_for("alerts.list_alerts"))
    except Exception as exc:  # for example, a model saved with a different scikit-learn version
        flash(f"The trained model could not be loaded ({type(exc).__name__}). "
              "Retrain it with 'python -m ml.train'.", "danger")
        return redirect(url_for("alerts.list_alerts"))

    summary = analysis.run_analysis(get_db(), detector)
    text = analysis.summary_text(summary)
    log_activity("run_analysis", resource_type="alert", details=text)
    flash(text, "success")
    return redirect(url_for("alerts.list_alerts"))