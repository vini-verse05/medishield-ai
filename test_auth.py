"""Part 2 verification: login, logout, sessions, RBAC, CSRF and redirects.
Uses a temporary database; your real instance/medishield.db is untouched.

Run:  python test_auth.py
"""
import os
import shutil
import tempfile

from app import create_app
from database import get_db, init_db
from security import has_permission, permission_required
from seed_data import DEMO_PASSWORD, seed_database

passed = 0


def check(name, condition):
    global passed
    if not condition:
        raise AssertionError(f"FAILED: {name}")
    passed += 1
    print(f"  [PASS] {name}")


def build_app(tmp):
    app = create_app({
        "DATABASE": os.path.join(tmp, "test.db"),
        "TESTING": True,
        "SECRET_KEY": "test-secret",
    })

    # Test-only routes that exercise clinical permissions, which have no real
    # pages until Part 3. They exist only inside this test script.
    @app.route("/_test/update-record", methods=["POST"])
    @permission_required("update_medical_record")
    def _update_record():
        return "ok"

    @app.route("/_test/register-patient", methods=["POST"])
    @permission_required("register_patient")
    def _register_patient():
        return "ok"

    with app.app_context():
        init_db()
        seed_database(get_db())
    return app


def post(client, url, data=None, csrf=True):
    """POST with a valid CSRF token (set directly in the test session)."""
    data = dict(data or {})
    if csrf:
        with client.session_transaction() as s:
            s["_csrf_token"] = "test-token"
        data["csrf_token"] = "test-token"
    return client.post(url, data=data)


def login(client, username, password=DEMO_PASSWORD, **extra):
    return post(client, "/login", {"username": username, "password": password, **extra})


def location(response):
    return response.headers.get("Location", "")


def db_value(app, sql, params=()):
    with app.app_context():
        return get_db().execute(sql, params).fetchone()[0]


def db_exec(app, sql, params=()):
    with app.app_context():
        db = get_db()
        db.execute(sql, params)
        db.commit()


def main():
    tmp = tempfile.mkdtemp()
    app = build_app(tmp)

    print("Valid login")
    for username, role in [("admin", "admin"), ("dr_sharma", "doctor"),
                           ("nurse_priya", "nurse"), ("recep_anita", "receptionist")]:
        c = app.test_client()
        r = login(c, username)
        check(f"{role}: login redirects to dashboard",
              r.status_code == 302 and location(r).endswith("/dashboard"))
        check(f"{role}: dashboard loads", c.get("/dashboard").status_code == 200)
    c = app.test_client()
    r = login(c, "admin")
    check("session cookie is HttpOnly",
          any("HttpOnly" in h for h in r.headers.getlist("Set-Cookie")))
    with c.session_transaction() as s:
        check("session stores user_id but no role", "user_id" in s and "role" not in s)

    print("Invalid login")
    c = app.test_client()
    r = login(c, "nurse_priya", "wrong-password")
    check("wrong password returns 401", r.status_code == 401)
    check("wrong password shows generic message", b"Invalid username or password" in r.data)
    check("no session after failed login", c.get("/dashboard").status_code == 302)
    r = login(c, "no_such_user", "whatever")
    check("unknown user gets the same generic message",
          r.status_code == 401 and b"Invalid username or password" in r.data)
    check("failed_login_count incremented",
          db_value(app, "SELECT failed_login_count FROM users WHERE username='nurse_priya'") == 1)
    login(c, "nurse_priya")
    check("failed_login_count reset after successful login",
          db_value(app, "SELECT failed_login_count FROM users WHERE username='nurse_priya'") == 0)

    print("Inactive account")
    db_exec(app, "UPDATE users SET is_active = 0 WHERE username='recep_anita'")
    c = app.test_client()
    check("inactive user cannot log in with correct password",
          login(c, "recep_anita").status_code == 401)
    db_exec(app, "UPDATE users SET is_active = 1 WHERE username='recep_anita'")

    print("CSRF")
    c = app.test_client()
    r = c.post("/login", data={"username": "admin", "password": DEMO_PASSWORD})
    check("login POST without CSRF token is rejected (400)", r.status_code == 400)
    r = c.post("/login", data={"username": "admin", "password": DEMO_PASSWORD,
                               "csrf_token": "forged"})
    check("login POST with wrong CSRF token is rejected (400)", r.status_code == 400)

    print("Logout")
    c = app.test_client()
    login(c, "admin")
    check("GET /logout is not allowed (405)", c.get("/logout").status_code == 405)
    check("still logged in after GET /logout", c.get("/dashboard").status_code == 200)
    check("logout POST without CSRF token is rejected",
          c.post("/logout").status_code == 400)
    check("still logged in after rejected logout", c.get("/dashboard").status_code == 200)
    r = post(c, "/logout")
    check("logout redirects to login", r.status_code == 302 and "/login" in location(r))
    check("dashboard blocked after logout", c.get("/dashboard").status_code == 302)

    print("Unauthenticated access")
    anon = app.test_client()
    for url in ["/dashboard", "/admin/users", "/db-check"]:
        r = anon.get(url)
        check(f"anonymous GET {url} redirects to login",
              r.status_code == 302 and "/login" in location(r))
    r = anon.get("/admin/users")
    check("redirect remembers the requested page", "next=" in location(r))
    check("anonymous POST to protected test route redirects to login",
          post(anon, "/_test/update-record").status_code == 302)

    print("Role-based access to admin pages")
    for username, role in [("dr_sharma", "doctor"), ("nurse_priya", "nurse"),
                           ("recep_anita", "receptionist")]:
        c = app.test_client()
        login(c, username)
        check(f"{role}: /admin/users forbidden (403)", c.get("/admin/users").status_code == 403)
        check(f"{role}: /db-check forbidden (403)", c.get("/db-check").status_code == 403)
    c = app.test_client()
    login(c, "admin")
    r = c.get("/admin/users")
    check("admin: /admin/users allowed", r.status_code == 200 and b"dr_sharma" in r.data)
    check("users page does not expose password hashes",
          b"scrypt" not in r.data and b"pbkdf2" not in r.data)
    r = c.get("/db-check")
    check("admin: /db-check allowed and shows data",
          r.status_code == 200 and b"MRN-0001" in r.data)

    print("Permission matrix (server-side)")
    cases = [
        ("dr_sharma",   "/_test/update-record",   200),
        ("nurse_priya", "/_test/update-record",   403),
        ("recep_anita", "/_test/update-record",   403),
        ("admin",       "/_test/update-record",   403),
        ("recep_anita", "/_test/register-patient", 200),
        ("nurse_priya", "/_test/register-patient", 403),
        ("dr_sharma",   "/_test/register-patient", 403),
    ]
    for username, url, expected in cases:
        c = app.test_client()
        login(c, username)
        check(f"{username} POST {url} -> {expected}", post(c, url).status_code == expected)
    check("receptionist has no medical-record access",
          not has_permission("receptionist", "view_medical_records"))
    check("admin has no clinical-record access",
          not has_permission("admin", "view_medical_records"))
    check("unknown role has no permissions", not has_permission("hacker", "view_users"))

    print("Tampering")
    c = app.test_client()
    login(c, "nurse_priya")
    with c.session_transaction() as s:
        s["role"] = "admin"
        s["is_admin"] = True
    check("editing role in the session does not grant admin access",
          c.get("/admin/users").status_code == 403)

    c = app.test_client()
    login(c, "dr_sharma")
    check("doctor session works before deactivation", c.get("/dashboard").status_code == 200)
    db_exec(app, "UPDATE users SET is_active = 0 WHERE username='dr_sharma'")
    check("deactivated user's live session stops working",
          c.get("/dashboard").status_code == 302)
    db_exec(app, "UPDATE users SET is_active = 1 WHERE username='dr_sharma'")

    print("Redirect safety")
    c = app.test_client()
    r = login(c, "admin", next="https://evil.example.com/steal")
    check("absolute external 'next' is ignored",
          "evil.example.com" not in location(r) and location(r).endswith("/dashboard"))
    c = app.test_client()
    r = login(c, "admin", next="//evil.example.com")
    check("protocol-relative 'next' is ignored", "evil.example.com" not in location(r))
    c = app.test_client()
    r = login(c, "admin", next="/admin/users")
    check("safe local 'next' is honoured", location(r).endswith("/admin/users"))

    print("Security headers")
    c = app.test_client()
    login(c, "admin")
    r = c.get("/dashboard")
    check("X-Content-Type-Options set", r.headers.get("X-Content-Type-Options") == "nosniff")
    check("X-Frame-Options set", r.headers.get("X-Frame-Options") == "DENY")
    check("authenticated pages are not cacheable",
          "no-store" in r.headers.get("Cache-Control", ""))

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nAll {passed} checks passed.")


if __name__ == "__main__":
    main()