"""Dashboard tests: dashboard queries, role-aware dashboards, nav counter,
demo-credentials switch, graceful fallback and the multi-seed aggregation.
Uses a temporary database.

Run:  python test_dashboard.py
"""
import json
import os
import re
import shutil
import tempfile
from datetime import date

import dashboard_data
from app import create_app
from database import get_db, init_db
from ml.evaluate_multiseed import aggregate_runs
from seed_data import DEMO_PASSWORD, seed_database

passed = 0
TODAY = date(2025, 6, 10)


def check(name, condition):
    global passed
    if not condition:
        raise AssertionError(f"FAILED: {name}")
    passed += 1
    print(f"  [PASS] {name}")

def squash(data):
    """Collapse all whitespace so editor line-wrapping cannot break text matches."""
    return " ".join(data.decode("utf-8").split())

def post(client, url, data=None):
    with client.session_transaction() as s:
        s["_csrf_token"] = "test-token"
    return client.post(url, data={**(data or {}), "csrf_token": "test-token"})


def client_for(app, username):
    c = app.test_client()
    assert post(c, "/login", {"username": username, "password": DEMO_PASSWORD}).status_code == 302
    return c


def val(app, sql, params=()):
    with app.app_context():
        return get_db().execute(sql, params).fetchone()[0]


def add_log(db, username, role, action, ts, details=None, success=1):
    user_id = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()[0]
    db.execute(
        "INSERT INTO activity_logs (user_id, username_attempted, role, action, success, "
        "timestamp, details) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, username, role, action, success, ts, details))


def main():
    tmp = tempfile.mkdtemp()
    app = create_app({"DATABASE": os.path.join(tmp, "test.db"), "TESTING": True,
                      "SECRET_KEY": "test-secret"})
    with app.app_context():
        init_db()
        db = get_db()
        seed_database(db)
        uid = lambda name: db.execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]
        # UTC timestamps; local time is UTC+05:30, so these fall on the dates noted.
        add_log(db, "dr_sharma", "doctor", "login", "2025-06-10 05:00:00")            # 06-10
        add_log(db, "nurse_priya", "nurse", "login_failed", "2025-06-10 05:10:00", success=0)
        add_log(db, "nurse_priya", "nurse", "access_denied", "2025-06-10 05:20:00", success=0)
        add_log(db, "dr_sharma", "doctor", "view_patient", "2025-06-10 05:30:00",
                details="SAMPLE DATA")                                                # excluded
        add_log(db, "dr_sharma", "doctor", "view_patient", "2025-06-09 06:00:00")    # 06-09
        add_log(db, "dr_sharma", "doctor", "view_patient", "2025-06-09 06:10:00")    # 06-09
        add_log(db, "dr_sharma", "doctor", "view_patient", "2025-05-01 06:00:00")    # outside window
        for who, role, day, flagged in [("dr_sharma", "doctor", "2025-06-10", 0),
                                        ("nurse_priya", "nurse", "2025-06-10", 1),
                                        ("dr_sharma", "doctor", "2025-06-09", 0)]:
            db.execute(
                "INSERT INTO analysis_results (user_id, role, result_date, anomaly_score, "
                "threshold, is_anomaly, features_json) VALUES (?, ?, ?, 0.5, 0.6, ?, '{}')",
                (uid(who), role, day, flagged))
        for who, risk, day, status in [("nurse_priya", "high", "2025-06-10", "new"),
                                       ("dr_sharma", "critical", "2025-06-10", "new"),
                                       ("dr_sharma", "low", "2025-06-09", "reviewed")]:
            db.execute(
                "INSERT INTO alerts (user_id, anomaly_score, risk_level, explanation, status, "
                "alert_date, threshold) VALUES (?, 0.7, ?, 'test explanation', ?, ?, 0.6)",
                (uid(who), risk, status, day))
        db.commit()

        print("Admin dashboard data")
        data = dashboard_data.admin_dashboard(db, today=TODAY)
        c, ch = data["cards"], data["chart"]
        check("cards: users, patients and records counted",
              (c["active_users"], c["patients"], c["medical_records"]) == (6, 8, 12))
        check("cards: 3 events today (sample-marked row excluded)", c["events_today"] == 3)
        check("cards: 1 failed login and 1 denied request today",
              c["failed_logins_today"] == 1 and c["denied_today"] == 1)
        check("cards: 2 new alerts, both high or critical",
              c["new_alerts"] == 2 and c["high_critical_new"] == 2)
        check("chart covers 14 consecutive local days ending today",
              len(ch["days"]) == 14 and ch["days"][0] == "2025-05-28" and ch["days"][-1] == "2025-06-10")
        check("daily events: 2 yesterday, 3 today, event outside the window ignored",
              ch["events"][-2] == 2 and ch["events"][-1] == 3 and sum(ch["events"]) == 5)
        check("failed + denied events counted per day", ch["failures"][-1] == 2 and sum(ch["failures"]) == 2)
        check("flagged and normal user-days per day",
              (ch["flagged_days"][-1], ch["normal_days"][-1]) == (1, 1)
              and (ch["flagged_days"][-2], ch["normal_days"][-2]) == (0, 1))
        check("alerts by risk level (new)", ch["levels"] == ["low", "medium", "high", "critical"]
              and ch["alerts_new"] == [0, 0, 1, 1])
        check("alerts by risk level (reviewed)", ch["alerts_reviewed"] == [1, 0, 0, 0])
        roles = {r["role"]: r for r in data["roles"]}
        check("role table covers all four roles", set(roles) == {"admin", "doctor", "nurse", "receptionist"})
        check("role table: active users per role",
              [roles[r]["active_users"] for r in ("admin", "doctor", "nurse", "receptionist")] == [1, 2, 2, 1])
        check("role table: events, failures and flagged days",
              roles["doctor"]["events"] == 3 and roles["nurse"]["events"] == 2
              and roles["nurse"]["failures"] == 2 and roles["nurse"]["flagged_days"] == 1
              and roles["doctor"]["flagged_days"] == 0)
        check("recent alerts list only new alerts, critical first",
              len(data["recent_alerts"]) == 2 and data["recent_alerts"][0]["risk_level"] == "critical")
        check("recent activity excludes the SAMPLE DATA placeholder rows",
              len(data["recent_activity"]) == 6)
        check("last analysis time is reported", data["last_analysis"] is not None)

        print("Staff dashboard data")
        staff = dashboard_data.staff_dashboard(db, uid("dr_sharma"), True, True, today=TODAY)
        check("staff: counts shown when the role may see them",
              staff["patients"] == 8 and staff["medical_records"] == 12)
        check("staff: own activity only (1 today, 4 recent)",
              staff["my_events_today"] == 1 and len(staff["my_recent"]) == 4)
        limited = dashboard_data.staff_dashboard(db, uid("recep_anita"), True, False, today=TODAY)
        check("staff: records count hidden when not permitted",
              limited["patients"] == 8 and limited["medical_records"] is None)
        none_shown = dashboard_data.staff_dashboard(db, uid("recep_anita"), False, False, today=TODAY)
        check("staff: no counts at all when nothing is permitted",
              none_shown["patients"] is None and none_shown["medical_records"] is None)

    print("Dashboard pages")
    admin = client_for(app, "admin")
    doctor = client_for(app, "dr_sharma")
    anon = app.test_client()
    r = admin.get("/dashboard")
    check("admin sees the administrator dashboard with charts",
          r.status_code == 200 and b"Administrator dashboard" in r.data and b"chartActivity" in r.data)
    check("admin dashboard lists the alerted users and carries the warning",
          "nurse_priya" in squash(r.data) and "not proof of malicious intent" in squash(r.data))
    check("alert counter is shown in the navigation", b'id="nav-alert-count"' in r.data)
    found = re.search(rb'<script id="dash-data" type="application/json">(.*?)</script>', r.data, re.S)
    payload = json.loads(found.group(1)) if found else {}
    check("chart data is valid JSON with 14 days",
          len(payload.get("days", [])) == 14 and "events" in payload and "alerts_new" in payload)
    check("admin dashboard exposes no patient names", b"Aarav" not in r.data and b"Patel" not in r.data)
    r = doctor.get("/dashboard")
    check("doctor sees the staff dashboard, not admin charts",
          r.status_code == 200 and b"Your recent activity" in r.data and b"chartActivity" not in r.data)
    check("doctor sees no alert counter", b'id="nav-alert-count"' not in r.data)
    check("anonymous is redirected to login", anon.get("/dashboard").status_code == 302)
    before = val(app, "SELECT COALESCE(MAX(id), 0) FROM activity_logs")
    admin.get("/dashboard")
    doctor.get("/dashboard")
    check("viewing dashboards writes no activity-log rows",
          val(app, "SELECT COALESCE(MAX(id), 0) FROM activity_logs") == before)

    print("Demo-credentials switch")
    default_page = app.test_client().get("/login").data
    hidden_app = create_app({"DATABASE": os.path.join(tmp, "other.db"), "TESTING": True,
                             "SHOW_DEMO_CREDENTIALS": False})
    hidden_page = hidden_app.test_client().get("/login").data
    check("demo accounts are shown by default", b"Demo accounts" in default_page)
    check("demo accounts are hidden when switched off", b"Demo accounts" not in hidden_page)

    print("Graceful fallback when the alert tables are missing")
    with app.app_context():
        get_db().execute("DROP TABLE analysis_results")
        get_db().commit()
    r = admin.get("/dashboard")
    check("admin dashboard degrades to a clear message instead of crashing",
          r.status_code == 200 and b"migrate-db" in r.data)

    print("Multi-seed aggregation")

    def fake(precision, recall, fpr, scenarios):
        return {"overall": {"precision": precision, "recall": recall, "f1": 0.5, "fpr": fpr,
                            "roc_auc_pooled_margin": 0.9},
                "global_baseline_no_roles": {"precision": 0.5, "recall": 0.5, "f1": 0.5,
                                             "fpr": 0.05, "roc_auc": None},
                "by_scenario": scenarios}

    agg = aggregate_runs([
        fake(0.8, 0.6, 0.02, {"bulk_access": {"n": 10, "detected": 9, "recall": 0.9}}),
        fake(0.6, 0.8, 0.04, {"bulk_access": {"n": 10, "detected": 7, "recall": 0.7},
                              "subtle_snooping": {"n": 4, "detected": 1, "recall": 0.25}}),
    ])
    rec = agg["per_role_models"]["recall"]
    check("mean and standard deviation are computed",
          abs(rec["mean"] - 0.7) < 1e-9 and abs(rec["std"] - 0.1) < 1e-9 and agg["n_runs"] == 2)
    check("missing values (None) are skipped, not averaged",
          agg["global_baseline"]["roc_auc"]["mean"] is None and agg["global_baseline"]["roc_auc"]["n"] == 0)
    check("scenario recall is pooled over runs",
          agg["recall_by_scenario"]["bulk_access"]["n"] == 20
          and abs(agg["recall_by_scenario"]["bulk_access"]["recall"] - 0.8) < 1e-9
          and abs(agg["recall_by_scenario"]["subtle_snooping"]["recall"] - 0.25) < 1e-9)
    expected = 0.7 * 0.01 / (0.7 * 0.01 + 0.03 * 0.99)
    check("projected precision follows the documented formula",
          abs(agg["projected_precision"][0]["precision"] - expected) < 1e-9)

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nAll {passed} checks passed.")


if __name__ == "__main__":
    main()