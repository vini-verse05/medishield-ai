"""The one place where activity-log rows are written. Called only from server
code; there is deliberately no function or route that updates or deletes logs."""
from flask import g, request

from database import get_db

_CURRENT_USER = object()  # sentinel: "use g.user"


def log_activity(action, *, user=_CURRENT_USER, username_attempted=None,
                 resource_type=None, resource_id=None, records_accessed=0,
                 success=True, details=None):
    """Insert one activity-log row and commit.

    `user` defaults to the logged-in user (g.user). Pass a user row explicitly
    (e.g. during login) or None (unknown user). Because this commits, any
    data change made earlier in the same request is committed atomically with
    its log entry.

    `details` must never contain patient data, only metadata such as field names.
    """
    if user is _CURRENT_USER:
        user = g.get("user")

    user_id = user["id"] if user is not None else None
    role = user["role"] if user is not None else None
    if user is not None and not username_attempted:
        username_attempted = user["username"]
    if username_attempted:
        username_attempted = username_attempted[:64]

    db = get_db()
    db.execute(
        "INSERT INTO activity_logs (user_id, username_attempted, role, action, "
        "resource_type, resource_id, records_accessed, success, ip_address, "
        "user_agent, details) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id, username_attempted, role, action, resource_type, resource_id,
            records_accessed, 1 if success else 0, request.remote_addr,
            request.headers.get("User-Agent", "")[:200],
            details[:500] if details else None,
        ),
    )
    db.commit()