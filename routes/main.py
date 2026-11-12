import sqlite3

from flask import Blueprint, flash, g, jsonify, render_template

import dashboard_data
from database import get_db
from security import (ROLE_PERMISSIONS, has_permission, login_required,
                      permission_required)

bp = Blueprint("main", __name__)

TABLES = ["users", "patients", "medical_records", "activity_logs", "alerts"]


@bp.route("/")
def index():
    return render_template("index.html")


@bp.route("/health")
def health():
    try:
        get_db().execute("SELECT 1").fetchone()
        return jsonify(status="ok", database="connected")
    except sqlite3.Error:
        return jsonify(status="error", database="unavailable"), 500


@bp.route("/dashboard")
@login_required
def dashboard():
    role = g.user["role"]
    db = get_db()

    if has_permission(role, "view_alerts"):
        try:
            data = dashboard_data.admin_dashboard(db)
            return render_template("dashboard_admin.html", data=data)
        except sqlite3.OperationalError:
            flash("The dashboard needs the latest database schema. "
                  "Run: flask --app app migrate-db", "danger")

    staff = dashboard_data.staff_dashboard(
        db, g.user["id"],
        include_patients=has_permission(role, "view_patients"),
        include_records=has_permission(role, "view_medical_records"),
    )
    permissions = sorted(ROLE_PERMISSIONS.get(role, set()))
    return render_template("dashboard.html", permissions=permissions, staff=staff)


# Verification page from Part 1, restricted to administrators.
@bp.route("/db-check")
@permission_required("view_db_check")
def db_check():
    db = get_db()
    try:
        counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
        users = db.execute(
            "SELECT id, username, full_name, role, is_active FROM users ORDER BY id"
        ).fetchall()
        patients = db.execute(
            "SELECT p.mrn, p.first_name || ' ' || p.last_name AS name, "
            "COUNT(m.id) AS record_count "
            "FROM patients p LEFT JOIN medical_records m ON m.patient_id = p.id "
            "GROUP BY p.id ORDER BY p.id"
        ).fetchall()
        logs = db.execute(
            "SELECT l.timestamp, COALESCE(u.username, l.username_attempted) AS actor, "
            "l.action, l.resource_type, l.resource_id, l.success "
            "FROM activity_logs l LEFT JOIN users u ON u.id = l.user_id "
            "ORDER BY l.id DESC LIMIT 10"
        ).fetchall()
    except sqlite3.OperationalError as exc:
        return render_template("db_check.html", error=str(exc)), 500

    return render_template(
        "db_check.html", error=None, counts=counts, users=users,
        patients=patients, logs=logs,
    )