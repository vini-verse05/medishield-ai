"""Alert tests: risk mapping, explanations, database migration, the full
workflow (user action -> ML result -> alert -> admin review) and access control.
Uses a temporary database. It trains a small model on synthetic data first,
so it takes a short while.

Run:  python test_alerts.py
"""
import json
import os
import shutil
import sqlite3
import tempfile

import numpy as np
import pandas as pd

import analysis
from app import create_app
from database import get_db, init_db, migrate_db
from ml import settings
from ml.detector import Detector
from ml.evaluate import label_features
from ml.explain import (LEVELS, build_explanation, build_signals, determine_risk,
                        score_band, score_ratio)
from ml.features import extract_features
from ml.generate_data import generate
from ml.train import fit_bundle
from seed_data import DEMO_PASSWORD, seed_database

passed = 0
ALERT_ROW = b'class="alert-row"'
BASE_DATE = "2025-06-02"


def check(name, condition):
    global passed
    if not condition:
        raise AssertionError(f"FAILED: {name}")
    passed += 1
    print(f"  [PASS] {name}")


# ------------------------------------------------------------------- helpers
def post(client, url, data=None, csrf=True):
    data = dict(data or {})
    if csrf:
        with client.session_transaction() as s:
            s["_csrf_token"] = "test-token"
        data["csrf_token"] = "test-token"
    return client.post(url, data=data)


def login(client, username, password=DEMO_PASSWORD):
    return post(client, "/login", {"username": username, "password": password})


def client_for(app, username):
    c = app.test_client()
    assert login(c, username).status_code == 302, f"login failed for {username}"
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
    """Test-only: move one user's new log rows to a fixed UTC time so the test
    does not depend on the time of day (or midnight) when it is run."""
    with app.app_context():
        db = get_db()
        db.execute(
            "UPDATE activity_logs SET timestamp = "
            "datetime(?, '+' || ((id - ?) * ?) || ' seconds') "
            "WHERE user_id = ? AND id > ?",
            (base_utc, after_id, step_seconds, uid(app, username), after_id))
        db.commit()


def baseline_for():
    base = {c: {"median": 10.0, "p95": 20.0, "std": 3.0} for c in settings.FEATURE_COLUMNS}
    base["failed_logins"] = {"median": 0.0, "p95": 1.0, "std": 0.3}
    base["login_hour"] = {"median": 9.0, "p95": 11.0, "std": 1.0}
    return base


def feats(**over):
    d = {c: 10.0 for c in settings.FEATURE_COLUMNS}
    d.update({
        "login_hour": 9.0,
        "failed_logins": 0.0,
        "denied_actions": 0.0,
        "off_hours_actions": 0.0,
        "records_accessed": 10.0,
    })
    d.update(over)
    return d


OLD_ALERTS_SQL = """
CREATE TABLE alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, activity_log_id INTEGER, user_id INTEGER,
    anomaly_score REAL,
    risk_level TEXT NOT NULL CHECK (risk_level IN ('low','medium','high','critical')),
    explanation TEXT,
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','reviewed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    reviewed_by INTEGER, reviewed_at TEXT);
"""


def main():
    # ----------------------------------------------------------- risk mapping
    print("Risk mapping and explanations (pure logic)")
    thr, ref = 0.50, 0.70
    check("ratio is 0 at the threshold", score_ratio(thr, thr, ref) == 0.0)
    check("ratio is 1 at the training maximum", score_ratio(ref, thr, ref) == 1.0)
    check("small excess -> low", score_band(score_ratio(0.52, thr, ref)) == "low")
    check("medium excess -> medium", score_band(score_ratio(0.58, thr, ref)) == "medium")
    check("large excess -> high", score_band(score_ratio(0.65, thr, ref)) == "high")
    check("at training maximum -> critical", score_band(score_ratio(ref, thr, ref)) == "critical")
    check("above training maximum -> critical", score_band(score_ratio(0.9, thr, ref)) == "critical")
    check("degenerate span is handled",
          score_ratio(0.6, 0.5, 0.5) == 1.0 and score_ratio(0.4, 0.5, 0.5) == 0.0)

    base = baseline_for()
    r = determine_risk(0.52, thr, ref, feats(), base)
    
    check("no escalation for a plain low score", r["risk_level"] == "low" and not r["escalated"])
    r = determine_risk(0.52, thr, ref, feats(failed_logins=12.0), base)
    check("12 failed logins escalate low -> medium",
          r["risk_level"] == "medium" and r["escalated"] and r["base_level"] == "low")
    r = determine_risk(0.52, thr, ref, feats(denied_actions=11.0), base)
    check("11 denied requests escalate one level", r["risk_level"] == "medium")
    r = determine_risk(0.52, thr, ref, feats(off_hours_actions=3.0, records_accessed=500.0), base)
    check("off-hours + high volume escalates one level", r["risk_level"] == "medium")
    r = determine_risk(0.9, thr, ref, feats(failed_logins=30.0), base)
    check("critical never escalates past critical",
          r["risk_level"] == "critical" and not r["escalated"])
    check("every level is a valid database value", set(LEVELS) == {"low", "medium", "high", "critical"})

    check("typical day has no signals", build_signals(feats(), base) == [])
    s = build_signals(feats(records_accessed=500.0), base)
    check("a high measure becomes the top signal",
          s[0]["feature"] == "records_accessed" and "500" in s[0]["text"])
    s = build_signals(feats(login_hour=3.0), base)
    check("a 3am login becomes a signal",
          s[0]["feature"] == "login_hour" and "03:00" in s[0]["text"])
    many = feats(records_accessed=100.0, patients_viewed=100.0, list_search_actions=100.0,
                 write_actions=100.0, total_events=100.0, peak_hourly_events=100.0)
    check("at most 4 signals are listed", len(build_signals(many, base)) == 4)
    text = build_explanation("nurse", [], [])
    check("explanation without signals is still informative and cautious",
          "nurse" in text and "not proof of malicious intent" in text)
    text = build_explanation("nurse", s, ["12 failed logins in one day"])
    check("explanation lists signals and escalation reasons",
          "03:00" in text and "raised one step" in text and "not proof" in text)

    tmp = tempfile.mkdtemp()

    # -------------------------------------------------------------- migration
    print("Database migration from an older schema")
    old_path = os.path.join(tmp, "old.db")
    conn = sqlite3.connect(old_path)
    conn.executescript(OLD_ALERTS_SQL)
    conn.execute("INSERT INTO alerts (risk_level) VALUES ('low')")
    conn.commit()
    conn.close()
    old_app = create_app({"DATABASE": old_path, "TESTING": True})
    with old_app.app_context():
        migrate_db()
        db = get_db()
        cols = {r["name"] for r in db.execute("PRAGMA table_info(alerts)")}
        check("alerts gained alert_date, threshold, signals_json",
              {"alert_date", "threshold", "signals_json"} <= cols)
        tables = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        check("analysis_results table was created", "analysis_results" in tables)
        check("existing alert row was preserved",
              db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1)
        migrate_db()
        check("running the migration twice is harmless",
              db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1)

    # ------------------------------------------------- model + app for workflow
    print("Preparing a small model and a fresh app (takes a moment)")
    events, labels, _ = generate(seed=7, n_days=90, train_days=60)
    labeled = label_features(extract_features(events), labels)
    train = labeled[labeled["split"] == "train"]
    detector = Detector(fit_bundle(train))

    app = create_app({"DATABASE": os.path.join(tmp, "test.db"), "TESTING": True,
                      "SECRET_KEY": "test-secret", "ML_DETECTOR": detector})
    with app.app_context():
        init_db()
        seed_database(get_db())

    # ---------------------------------------------------- real user activity
    print("Generating activity through the real app routes")
    pids = [r["id"] for r in rows(app, "SELECT id FROM patients ORDER BY id")]

    m = mark(app)
    nurse = client_for(app, "nurse_priya")
    for _ in range(150):
        nurse.get("/patients")
    for _ in range(60):
        nurse.get("/patients?q=a")
    for i in range(90):
        nurse.get(f"/patients/{pids[i % len(pids)]}")
    normalize(app, "nurse_priya", m, f"{BASE_DATE} 04:30:00", 5)   # local 10:00 IST
    check("bulk session produced 301 log rows for nurse_priya",
          val(app, "SELECT COUNT(*) FROM activity_logs WHERE user_id = ? AND id > ?",
              (uid(app, "nurse_priya"), m)) == 301)

    m = mark(app)
    attacker = app.test_client()
    for _ in range(20):
        login(attacker, "recep_anita", "wrong-password")
    normalize(app, "recep_anita", m, f"{BASE_DATE} 05:00:00", 5)
    check("20 failed logins were logged for recep_anita",
          val(app, "SELECT COUNT(*) FROM activity_logs WHERE user_id = ? AND action = 'login_failed'",
              (uid(app, "recep_anita"),)) == 20)

    # A genuinely normal day: the most typical doctor day from the training data,
    # inserted into activity_logs for dr_sharma.
    scored_doctors = detector.score(train[train["role"] == "doctor"])
    best = scored_doctors.loc[scored_doctors["anomaly_score"].idxmin()]
    sdate, suser = best["date"], int(best["user_id"])
    local_dates = (pd.to_datetime(events["timestamp"])
                   + pd.Timedelta(minutes=settings.TIMEZONE_OFFSET_MINUTES)).dt.strftime("%Y-%m-%d")
    day_events = events[(events["user_id"] == suser) & (local_dates == sdate)]
    dr_id = uid(app, "dr_sharma")
    with app.app_context():
        db = get_db()
        for e in day_events.itertuples(index=False):
            db.execute(
                "INSERT INTO activity_logs (user_id, username_attempted, role, action, "
                "resource_type, resource_id, records_accessed, success, timestamp) "
                "VALUES (?, 'dr_sharma', 'doctor', ?, ?, ?, ?, ?, ?)",
                (dr_id, e.action,
                 None if pd.isna(e.resource_type) else e.resource_type,
                 None if pd.isna(e.resource_id) else int(e.resource_id),
                 int(e.records_accessed), int(e.success), e.timestamp))
        db.commit()
    check("a typical doctor day was loaded into activity_logs", len(day_events) > 0)

    # --------------------------------------------------------------- analysis
    print("Analysis: logs -> features -> model -> stored results")
    with app.app_context():
        summary = analysis.run_analysis(get_db(), detector)
    check("3 user-days were analysed (seed placeholder rows are excluded)",
          summary["days_analyzed"] == 3)
    results = {r["username"]: r for r in rows(
        app, "SELECT r.*, u.username FROM analysis_results r JOIN users u ON u.id = r.user_id")}
    check("results are stored for every analysed day", set(results) == {"nurse_priya", "recep_anita", "dr_sharma"})
    check("no result was created from the SEED sample logs",
          val(app, "SELECT COUNT(*) FROM analysis_results WHERE user_id = ?", (dr_id,)) == 1)
    feat = json.loads(results["dr_sharma"]["features_json"])
    expected = labeled[(labeled["user_id"] == suser) & (labeled["date"] == sdate)].iloc[0]
    check("features computed from the database match the original features",
          all(np.isclose(feat[c], expected[c]) for c in settings.FEATURE_COLUMNS))
    check("the bulk-access day is flagged by the model", results["nurse_priya"]["is_anomaly"] == 1)
    check("the credential-attack day is flagged by the model", results["recep_anita"]["is_anomaly"] == 1)
    check("the typical doctor day is not flagged", results["dr_sharma"]["is_anomaly"] == 0)
    check("stored scores come from the model (flagged score > threshold)",
          results["nurse_priya"]["anomaly_score"] > results["nurse_priya"]["threshold"]
          > 0 and results["dr_sharma"]["anomaly_score"] <= results["dr_sharma"]["threshold"])
    check("bulk day scores higher than the typical day",
          results["nurse_priya"]["anomaly_score"] > results["dr_sharma"]["anomaly_score"])

    print("Alerts")
    alerts = {r["username"]: r for r in rows(
        app, "SELECT a.*, u.username FROM alerts a JOIN users u ON u.id = a.user_id")}
    check("alerts exist only for the two flagged days", set(alerts) == {"nurse_priya", "recep_anita"})
    for name, a in alerts.items():
        check(f"{name}: alert is new, dated and has a valid risk level",
              a["status"] == "new" and a["alert_date"] == BASE_DATE and a["risk_level"] in LEVELS)
        check(f"{name}: alert stores score above threshold",
              a["anomaly_score"] > a["threshold"])
        check(f"{name}: alert links to one of that user's log rows",
              val(app, "SELECT user_id FROM activity_logs WHERE id = ?", (a["activity_log_id"],))
              == a["user_id"])
        check(f"{name}: explanation carries the caution",
              "not proof of malicious intent" in a["explanation"])
    sig_priya = json.loads(alerts["nurse_priya"]["signals_json"])
    check("bulk alert lists volume signals",
          any(s["feature"] in ("records_accessed", "total_events", "peak_hourly_events",
                               "list_search_actions") for s in sig_priya))
    sig_anita = json.loads(alerts["recep_anita"]["signals_json"])
    check("attack alert lists the failed-login signal",
          any(s["feature"] == "failed_logins" for s in sig_anita))
    check("attack alert is escalated or already critical",
          "raised one step" in alerts["recep_anita"]["explanation"]
          or alerts["recep_anita"]["risk_level"] == "critical")
    check("no patient names appear in alert text",
          all("Aarav" not in a["explanation"] for a in alerts.values()))

    print("Duplicate prevention")
    count_before = val(app, "SELECT COUNT(*) FROM alerts")
    with app.app_context():
        again = analysis.run_analysis(get_db(), detector)
    check("second run creates no new alerts", again["alerts_created"] == 0)
    check("second run updates the existing alerts in place",
          again["alerts_updated"] == again["flagged"])
    check("alert count is unchanged", val(app, "SELECT COUNT(*) FROM alerts") == count_before)
    check("analysis_results has no duplicate user-days",
          val(app, "SELECT COUNT(*) FROM (SELECT 1 FROM analysis_results "
                   "GROUP BY user_id, result_date HAVING COUNT(*) > 1)") == 0)
    try:
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO alerts (user_id, risk_level, alert_date) VALUES (?, 'low', ?)",
                       (uid(app, "nurse_priya"), BASE_DATE))
            db.commit()
        check("database rejects a duplicate alert for the same user-day", False)
    except sqlite3.IntegrityError:
        check("database rejects a duplicate alert for the same user-day", True)

    # ------------------------------------------------------------ admin web UI
    print("Administrator alert pages and review")
    admin = client_for(app, "admin")
    doctor = client_for(app, "dr_sharma")
    nurse2 = client_for(app, "nurse_rahul")
    anon = app.test_client()
    priya_alert = alerts["nurse_priya"]["id"]
    anita_alert = alerts["recep_anita"]["id"]

    r = admin.get("/admin/alerts")
    check("admin can open the alerts page and sees both users",
          r.status_code == 200 and b"nurse_priya" in r.data and b"recep_anita" in r.data)
    check("alerts page shows the investigation-warning text", b"not proof of malicious intent" in r.data)
    check("alerts are listed as rows", r.data.count(ALERT_ROW) >= 2)
    check("reviewed filter starts empty",
          admin.get("/admin/alerts?status=reviewed").data.count(ALERT_ROW) == 0)
    r = admin.get(f"/admin/alerts/{priya_alert}")
    check("alert detail shows explanation and review button",
          r.status_code == 200 and b"Contributing signals" in r.data and b"Mark as reviewed" in r.data)
    check("missing alert returns 404", admin.get("/admin/alerts/99999").status_code == 404)

    check("doctor cannot open alerts (403)", doctor.get("/admin/alerts").status_code == 403)
    check("doctor cannot open an alert (403)", doctor.get(f"/admin/alerts/{priya_alert}").status_code == 403)
    check("anonymous is redirected to login", anon.get("/admin/alerts").status_code == 302)
    check("nurse cannot review an alert (403)",
          post(nurse2, f"/admin/alerts/{anita_alert}/review").status_code == 403)
    check("blocked review changed nothing",
          val(app, "SELECT status FROM alerts WHERE id = ?", (anita_alert,)) == "new")
    results_before = val(app, "SELECT COUNT(*) FROM analysis_results")
    check("doctor cannot run analysis (403)", post(doctor, "/admin/alerts/run").status_code == 403)
    check("blocked run stored nothing",
          val(app, "SELECT COUNT(*) FROM analysis_results") == results_before)
    check("review without CSRF token is rejected (400)",
          admin.post(f"/admin/alerts/{priya_alert}/review").status_code == 400)

    r = post(admin, f"/admin/alerts/{priya_alert}/review")
    row = rows(app, "SELECT * FROM alerts WHERE id = ?", (priya_alert,))[0]
    check("admin can mark an alert reviewed",
          r.status_code == 302 and row["status"] == "reviewed"
          and row["reviewed_by"] == uid(app, "admin") and row["reviewed_at"])
    reviewed_at, reviewed_score = row["reviewed_at"], row["anomaly_score"]
    check("reviewed filter now shows exactly that alert",
          admin.get("/admin/alerts?status=reviewed").data.count(ALERT_ROW) == 1)
    check("new filter no longer lists the reviewed user",
          b"nurse_priya" not in admin.get("/admin/alerts?status=new").data)
    post(admin, f"/admin/alerts/{priya_alert}/review")
    check("reviewing twice keeps the original review time",
          val(app, "SELECT reviewed_at FROM alerts WHERE id = ?", (priya_alert,)) == reviewed_at)

    r = post(admin, "/admin/alerts/run")
    check("admin can run analysis from the web", r.status_code == 302)
    row = rows(app, "SELECT * FROM alerts WHERE id = ?", (priya_alert,))[0]
    check("a reviewed alert is not reopened or changed by a new run",
          row["status"] == "reviewed" and row["reviewed_at"] == reviewed_at
          and row["anomaly_score"] == reviewed_score)
    check("still no duplicate alerts for any user-day",
          val(app, "SELECT COUNT(*) FROM (SELECT 1 FROM alerts WHERE alert_date IS NOT NULL "
                   "GROUP BY user_id, alert_date HAVING COUNT(*) > 1)") == 0)

    app.config["ML_DETECTOR"] = None
    app.config["ML_MODEL_PATH"] = os.path.join(tmp, "missing.joblib")
    post(admin, "/admin/alerts/run")
    page = admin.get("/admin/alerts").data
    check("a missing model shows a helpful message, not a crash",
          b"trained model could not be found" in page)
    app.config["ML_DETECTOR"] = detector

    print("Command line")
    runner = app.test_cli_runner()
    res = runner.invoke(args=["analyze"])
    check("flask analyze runs and prints a summary",
          res.exit_code == 0 and "Analyzed" in res.output)
    res = runner.invoke(args=["analyze", "--since", "not-a-date"])
    check("flask analyze rejects a bad --since date", res.exit_code != 0)

    print("Audit trail of the new actions")
    for action in ("view_alerts", "view_alert", "review_alert", "run_analysis"):
        check(f"'{action}' was written to the audit log",
              val(app, "SELECT COUNT(*) FROM activity_logs WHERE action = ?", (action,)) >= 1)
    check("blocked attempts were logged as access_denied",
          val(app, "SELECT COUNT(*) FROM activity_logs WHERE action = 'access_denied' "
                   "AND details LIKE '%alerts%'") >= 3)

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nAll {passed} checks passed.")


if __name__ == "__main__":
    main()