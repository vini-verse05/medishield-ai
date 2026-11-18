"""Security layer: who is logged in, what each role may do, CSRF protection
and security headers. Every permission check happens on the server, so
hiding a menu link is never the only protection."""
import functools
import hmac
import secrets

from flask import abort, flash, g, redirect, request, session, url_for

from audit import log_activity
from database import get_db

# What each role may do. Routes check these names; nothing else grants access.
ROLE_PERMISSIONS = {
    "admin": {
        "view_users",
        "view_audit_logs",
        "view_alerts",
        "review_alerts",
        "view_db_check",
    },
    "doctor": {
        "view_patients",
        "view_patient_details",
        "view_medical_records",
        "update_patient",
        "create_medical_record",
        "update_medical_record",
    },
    "nurse": {
        "view_patients",
        "view_patient_details",
        "view_medical_records",
        "create_medical_record",
    },
    "receptionist": {
        "view_patients",          # demographics only (enforced in routes/patients.py)
        "register_patient",
        "update_patient_contact",
    },
}


def has_permission(role, permission):
    """True if the role has the named permission."""
    return permission in ROLE_PERMISSIONS.get(role, set())


# ---------------------------------------------------------------- current user
def load_current_user():
    """Runs before every request. Sets g.user from the database, or None."""
    g.user = None
    user_id = session.get("user_id")
    if user_id is None:
        return
    user = get_db().execute(
        "SELECT id, username, full_name, role, department, is_active "
        "FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if user is None or not user["is_active"]:
        session.clear()  # deleted or deactivated account: drop the session
        return
    g.user = user


# ------------------------------------------------------------------ decorators
def login_required(view):
    """Send visitors who are not logged in to the login page."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Please log in to continue.", "warning")
            if request.method == "GET":
                target = request.full_path if request.query_string else request.path
                return redirect(url_for("auth.login", next=target))
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapped


def permission_required(*permissions):
    """Allow the request if the user's role has ANY of the listed permissions.
    A denied request is written to the audit log before the 403 is returned."""
    def decorator(view):
        @functools.wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if not any(has_permission(g.user["role"], p) for p in permissions):
                log_activity(
                    "access_denied",
                    resource_type="route",
                    success=False,
                    details=f"{request.method} {request.path} requires "
                            f"{' or '.join(permissions)}",
                )
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


# ------------------------------------------------------------------------ CSRF
def get_csrf_token():
    """Return this session's CSRF token, creating it on first use."""
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_urlsafe(32)
    return session["_csrf_token"]


def csrf_protect():
    """Reject any state-changing request that lacks a valid CSRF token."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRFToken", "")
        expected = session.get("_csrf_token", "")
        if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
            log_activity("csrf_failure", success=False,
                         details=f"{request.method} {request.path}")
            abort(400, description="Invalid or missing CSRF token. Reload the page and try again.")


# ------------------------------------------------------------- app integration
def inject_template_helpers():
    """Make csrf_token() and can(permission) available in every template."""
    def can(permission):
        user = g.get("user")
        return user is not None and has_permission(user["role"], permission)

    return {"csrf_token": get_csrf_token, "can": can}


def add_security_headers(response):
    """Add browser protections; pages for logged-in users are never cached."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if g.get("user") is not None:
        response.headers["Cache-Control"] = "no-store"
    return response


def init_app(app):
    """Turn on user loading, CSRF checks, template helpers and security headers."""
    app.before_request(load_current_user)  # must run before csrf_protect
    app.before_request(csrf_protect)
    app.context_processor(inject_template_helpers)
    app.after_request(add_security_headers)