"""Scenario tests: (1) every role against every major page, (2) end-to-end
normal and suspicious scenarios driven through the REAL routes and scored by a
REAL Isolation Forest (a small model is trained here, so this takes a while).
Uses temporary databases.

Run:  python test_scenarios.py
"""
import os
import shutil
import tempfile
from datetime import date

import analysis
import dashboard_data
from app import create_app
from database import get_db, init_db
from ml.detector import Detector
from ml.evaluate import label_features
from ml.features import extract_features
from ml.generate_data import generate
from ml.train import fit_bundle
from seed_data import DEMO_PASSWORD, seed_database

passed = 0
DAY = "2025-06-02"

PATIENT_FORM = {
    "first_name": "Aarav", "last_name": "Patel", "date_of_birth": "1985-03-14",
    "gender": "Male", "phone": "555-0101", "address": "12 Sample Street",
    "blood_group": "B+", "allergies": "Penicillin",
}
NOTE = {"record_type": "Nursing Note", "diagnosis": "Vitals stable",
        "treatment": "Observation", "notes": "SAMPLE note"}

MATRIX_URLS = ["/dashboard", "/patients", "/patients/1", "/patients/new", "/patients/1/edit",
               "/patients/1/records/new", "/records/1/edit", "/admin/users",
               "/admin/audit-logs", "/admin/alerts", "/db-check"]
EXPECTED = {
    "admin":        [200, 403, 403, 403, 403, 403, 403, 200, 200, 200, 200],
    "doctor":       [200, 200, 200, 403, 200, 200, 200, 403, 403, 403, 403],
    "nurse":        [200, 200, 200, 403, 403, 200, 403, 403, 403, 403, 403],
    "receptionist": [200, 200, 200, 200, 200, 403, 403, 403, 403, 403, 403],
}
USERS = {"admin": "admin", "doctor": "dr_sharma", "nurse": "nurse_priya",
         "receptionist": "recep_anita"}


def check(name, condition):
    global passed
    if not condition:
        raise AssertionError(f"FAILED: {name}")
    passed += 1
    print(f"  [PASS] {name}")


def post(client, url, data=None):
    with client.session_transaction() as s:
        s["_csrf_token"] = "test-token"
    return client.post(url, data={**(data or {}), "csrf_token": "test-token"})


def client_for(app, username):
    c = app.test_client()
    assert post(c, "/login", {"username": username, "password": DEMO_PASSWORD}).status_code == 302
    return c


def rows(app, sql, params=()):
    with app.app_context():
        return [dict(r) for r in get_db().execute(sql, params).fetchall()]


def val(app, sql, params=()):
    with app.app_context():
        return get_db().execute(sql, params).fetchone()[0]


def mark(app):
    return val(app, "SELECT COALESCE(MAX(id), 0) FROM activity_logs")


def uid(app, username):
    return val(app, "SELECT id FROM users WHERE username = ?", (username,))


def normalize(app, username, after_id, base_utc, step_seconds):
    """Test-only: move one user's new log rows to a fixed UTC time so results do
    not depend on when the test is run. Application code never edits logs."""
    with app.app_context():
        db = get_db()
        db.execute(
            "UPDATE activity_logs SET timestamp = "
            "datetime(?, '+' || ((id - ?) * ?) || ' seconds') WHERE user_id = ? AND id > ?",
            (base_utc, after_id, step_seconds, uid(app, username), after_id))
        db.commit()


def build_app(tmp, name, detector=None):
    cfg = {"DATABASE": os.path.join(tmp, name), "TESTING": True, "SECRET_KEY": "test-secret"}
    if detector is not None:
        cfg["ML_DETECTOR"] = detector
    app = create_app(cfg)
    with app.app_context():
        init_db()
        seed_database(get_db())
    return app


def main():
    tmp = tempfile.mkdtemp()

    # ------------------------------------------------------------ role matrix
    print("Every role against every major page")
    app = build_app(tmp, "matrix.db")
    for role, username in USERS.items():
        c = client_for(app, username)
        wrong = []
        for url, expected in zip(MATRIX_URLS, EXPECTED[role]):
            status = c.get(url).status_code
            if status != expected:
                wrong.append(f"{url}: got {status}, expected {expected}")
        check(f"{role}: all {len(MATRIX_URLS)} pages return the expected status"
              + (f" (wrong: {wrong})" if wrong else ""), not wrong)
        post(c, "/logout")
        check(f"{role}: logout ends the session", c.get("/dashboard").status_code == 302)
    anon = app.test_client()
    check("anonymous users are redirected from every protected page",
          all(anon.get(u).status_code == 302 for u in MATRIX_URLS))

    # ------------------------------------------------------- scenario setup
    print("Preparing a small model (takes a moment)")
    events, labels, _ = generate(seed=7, n_days=90, train_days=60)
    labeled = label_features(extract_features(events), labels)
    detector = Detector(fit_bundle(labeled[labeled["split"] == "train"]))
    app = build_app(tmp, "scenarios.db", detector)
    pids = [r["id"] for r in rows(app, "SELECT id FROM patients ORDER BY id")]
    base_mark = mark(app)

    print("Generating normal and suspicious activity through the real routes")
    # 1. Normal nurse day: 07:00 local, a typical mix of work.
    m = mark(app)
    c = client_for(app, "nurse_priya")
    for i in range(14):
        c.get(f"/patients/{pids[i % len(pids)]}")
    for _ in range(5):
        c.get("/patients?q=a")
    for _ in range(3):
        c.get("/patients")
    for i in range(4):
        post(c, f"/patients/{pids[i]}/records/new", NOTE)
    post(c, "/logout")
    normalize(app, "nurse_priya", m, f"{DAY} 01:30:00", 60)

    # 2. Privilege probing: a nurse keeps requesting admin pages.
    m = mark(app)
    c = client_for(app, "nurse_rahul")
    for _ in range(12):
        c.get("/admin/users")
    for _ in range(4):
        c.get("/admin/alerts")
    normalize(app, "nurse_rahul", m, f"{DAY} 04:30:00", 30)
    check("probing produced 16 access_denied log rows",
          val(app, "SELECT COUNT(*) FROM activity_logs WHERE user_id = ? AND action = 'access_denied'",
              (uid(app, "nurse_rahul"),)) == 16)

    # 3. Off-hours bulk access: a doctor reads 120 patients and searches at 02:00 local.
    m = mark(app)
    c = client_for(app, "dr_mehta")
    for i in range(120):
        c.get(f"/patients/{pids[i % len(pids)]}")
    for _ in range(30):
        c.get("/patients?q=a")
    normalize(app, "dr_mehta", m, "2025-06-01 20:30:00", 5)

    # 4. Mass modification: a doctor changes one record 55 times.
    m = mark(app)
    c = client_for(app, "dr_sharma")
    for i in range(55):
        post(c, "/patients/1/edit", dict(PATIENT_FORM, phone=f"555-{1000 + (i % 2) * 1000}"))
    normalize(app, "dr_sharma", m, f"{DAY} 03:30:00", 20)
    check("mass modification produced 55 update_patient log rows",
          val(app, "SELECT COUNT(*) FROM activity_logs WHERE user_id = ? AND action = 'update_patient'",
              (uid(app, "dr_sharma"),)) == 55)

    # ------------------------------------------------------------- analysis
    print("Analysis of the application-generated logs")
    with app.app_context():
        summary = analysis.run_analysis(get_db(), detector)
    check("exactly 4 user-days were analysed", summary["days_analyzed"] == 4)
    results = {r["username"]: r for r in rows(
        app, "SELECT r.*, u.username FROM analysis_results r JOIN users u ON u.id = r.user_id")}
    margin = {u: r["anomaly_score"] - r["threshold"] for u, r in results.items()}
    check("privilege probing is flagged", results["nurse_rahul"]["is_anomaly"] == 1)
    check("off-hours bulk access is flagged", results["dr_mehta"]["is_anomaly"] == 1)
    check("mass modification is flagged", results["dr_sharma"]["is_anomaly"] == 1)
    check("the normal nurse day scores below the probing and off-hours days",
          margin["nurse_priya"] < margin["nurse_rahul"] and margin["nurse_priya"] < margin["dr_mehta"])
    print(f"  (info) normal nurse day flagged: {bool(results['nurse_priya']['is_anomaly'])}; "
          f"margins: " + ", ".join(f"{u}={v:+.3f}" for u, v in margin.items()))

    alerts = {r["username"]: r for r in rows(
        app, "SELECT a.*, u.username FROM alerts a JOIN users u ON u.id = a.user_id")}
    check("alerts exist for the three suspicious users",
          {"nurse_rahul", "dr_mehta", "dr_sharma"} <= set(alerts))
    check("the probing alert is escalated to at least medium",
          alerts["nurse_rahul"]["risk_level"] in ("medium", "high", "critical"))
    signals = lambda u: {s["feature"] for s in __import__("json").loads(alerts[u]["signals_json"])}
    check("off-hours alert lists an off-hours signal",
          bool(signals("dr_mehta") & {"login_hour", "off_hours_actions"}))
    check("mass-modification alert lists the record-change signal",
          "write_actions" in signals("dr_sharma"))
    check("every alert carries the investigation caution",
          all("not proof of malicious intent" in a["explanation"] for a in alerts.values()))

    # ------------------------------------------------------------ dashboard
    print("Dashboard reflects the scenarios")
    total_rows = val(app, "SELECT COUNT(*) FROM activity_logs WHERE id > ?", (base_mark,))
    flagged = sum(r["is_anomaly"] for r in results.values())
    with app.app_context():
        data = dashboard_data.admin_dashboard(get_db(), today=date(2025, 6, 2))
    ch = data["chart"]
    check("events today equal the log rows the scenarios created",
          data["cards"]["events_today"] == total_rows and ch["events"][-1] == total_rows)
    check("failed/denied count equals the 16 probing requests", ch["failures"][-1] == 16)
    check("flagged and normal user-days match the model results",
          ch["flagged_days"][-1] == flagged and ch["normal_days"][-1] + ch["flagged_days"][-1] == 4)
    check("alert chart totals match the alerts table",
          sum(ch["alerts_new"]) == val(app, "SELECT COUNT(*) FROM alerts"))

    admin = client_for(app, "admin")
    page = admin.get("/dashboard")
    check("admin dashboard page shows all three alerted users",
          page.status_code == 200 and all(n.encode() in page.data
                                          for n in ("nurse_rahul", "dr_mehta", "dr_sharma")))
    post(admin, f"/admin/alerts/{alerts['nurse_rahul']['id']}/review")
    with app.app_context():
        after = dashboard_data.admin_dashboard(get_db(), today=date(2025, 6, 2))
    check("reviewing an alert moves it from new to reviewed on the dashboard",
          after["cards"]["new_alerts"] == data["cards"]["new_alerts"] - 1
          and sum(after["chart"]["alerts_reviewed"]) == 1)

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nAll {passed} checks passed.")


if __name__ == "__main__":
    main()