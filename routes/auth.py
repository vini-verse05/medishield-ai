from urllib.parse import urlparse

from flask import (Blueprint, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from database import get_db

bp = Blueprint("auth", __name__)

# Checked when the username does not exist, so response time does not reveal
# whether an account exists.
DUMMY_HASH = generate_password_hash("not-a-real-password")


def is_safe_next(target):
    """Allow only local paths, to prevent open redirects."""
    if not target or not target.startswith("/") or target.startswith("//"):
        return False
    if "\\" in target:
        return False
    parts = urlparse(target)
    return not parts.scheme and not parts.netloc


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(url_for("main.dashboard"))

    next_url = request.form.get("next") or request.args.get("next") or ""

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        db = get_db()
        user = db.execute(
            "SELECT id, password_hash, is_active FROM users WHERE username = ?",
            (username,),
        ).fetchone()

        stored_hash = user["password_hash"] if user else DUMMY_HASH
        password_ok = check_password_hash(stored_hash, password)

        if user is not None and password_ok and user["is_active"]:
            session.clear()  # new session on login (prevents session fixation)
            session["user_id"] = user["id"]
            session.permanent = True
            db.execute("UPDATE users SET failed_login_count = 0 WHERE id = ?", (user["id"],))
            db.commit()
            return redirect(next_url if is_safe_next(next_url) else url_for("main.dashboard"))

        if user is not None and not password_ok:
            db.execute(
                "UPDATE users SET failed_login_count = failed_login_count + 1 WHERE id = ?",
                (user["id"],),
            )
            db.commit()

        # Same message for unknown user, wrong password and inactive account.
        flash("Invalid username or password.", "danger")
        return render_template("login.html", next=next_url), 401

    return render_template("login.html", next=next_url)


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))