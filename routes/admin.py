"""Administrator pages: the user list and the audit log viewer."""
from math import ceil

from flask import Blueprint, render_template, request

from audit import log_activity
from database import get_db
from security import permission_required

bp = Blueprint("admin", __name__, url_prefix="/admin")

PAGE_SIZE = 50  # audit log entries per page


@bp.route("/users")
@permission_required("view_users")
def users():
    """List every account and its role. Password hashes are never selected."""
    rows = get_db().execute(
        "SELECT id, username, full_name, role, department, is_active, "
        "failed_login_count, created_at FROM users ORDER BY id"
    ).fetchall()
    return render_template("admin_users.html", users=rows)


@bp.route("/audit-logs")
@permission_required("view_audit_logs")
def audit_logs():
    """Read-only audit log with filters and paging. This blueprint has no route
    that changes or removes log entries."""
    db = get_db()
    action = request.args.get("action", "")
    username = request.args.get("user", "").strip()[:64]
    status = request.args.get("status", "")
    page = max(request.args.get("page", 1, type=int), 1)

    where, params = [], []
    if action:
        where.append("l.action = ?")
        params.append(action)
    if username:
        where.append("COALESCE(u.username, l.username_attempted) LIKE ?")
        params.append(f"%{username}%")
    if status == "success":
        where.append("l.success = 1")
    elif status == "failed":
        where.append("l.success = 0")

    clause = ("WHERE " + " AND ".join(where)) if where else ""
    base = "FROM activity_logs l LEFT JOIN users u ON u.id = l.user_id " + clause

    total = db.execute("SELECT COUNT(*) " + base, params).fetchone()[0]
    rows = db.execute(
        "SELECT l.id, l.timestamp, COALESCE(u.username, l.username_attempted) AS actor, "
        "l.role, l.action, l.resource_type, l.resource_id, l.records_accessed, "
        "l.success, l.ip_address, l.details " + base +
        " ORDER BY l.id DESC LIMIT ? OFFSET ?",
        params + [PAGE_SIZE, (page - 1) * PAGE_SIZE],
    ).fetchall()
    actions = [r[0] for r in db.execute(
        "SELECT DISTINCT action FROM activity_logs ORDER BY action")]

    log_activity("view_audit_logs", resource_type="activity_log",
                 details=f"page={page}; matching={total}")

    return render_template(
        "admin_audit_logs.html", rows=rows, actions=actions, action=action,
        username=username, status=status, page=page,
        pages=max(ceil(total / PAGE_SIZE), 1), total=total,
    )