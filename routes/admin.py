from flask import Blueprint, render_template

from database import get_db
from security import permission_required

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/users")
@permission_required("view_users")
def users():
    # password_hash is deliberately never selected.
    rows = get_db().execute(
        "SELECT id, username, full_name, role, department, is_active, "
        "failed_login_count, created_at FROM users ORDER BY id"
    ).fetchall()
    return render_template("admin_users.html", users=rows)