"""Part 1 verification: tests the schema, seed data, relationships and routes
against a temporary database (your real instance/medishield.db is untouched).

Run:  python test_db.py
"""
import os
import shutil
import sqlite3
import tempfile

from app import create_app
from database import get_db, init_db
from seed_data import seed_database

passed = 0


def check(name, condition):
    global passed
    if not condition:
        raise AssertionError(f"FAILED: {name}")
    passed += 1
    print(f"  [PASS] {name}")


def expect_error(name, func):
    try:
        func()
    except sqlite3.IntegrityError:
        check(name, True)
    else:
        check(name, False)


def main():
    tmp = tempfile.mkdtemp()
    app = create_app({"DATABASE": os.path.join(tmp, "test.db"), "TESTING": True})

    with app.app_context():
        print("Schema")
        init_db()
        db = get_db()
        tables = {r["name"] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ["users", "patients", "medical_records", "activity_logs", "alerts"]:
            check(f"table '{t}' exists", t in tables)

        print("Seeding")
        seed_database(db)
        check("6 users inserted", db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 6)
        check("8 patients inserted", db.execute("SELECT COUNT(*) FROM patients").fetchone()[0] == 8)
        check("12 medical records inserted",
              db.execute("SELECT COUNT(*) FROM medical_records").fetchone()[0] == 12)
        seed_database(db)
        check("re-seeding is skipped (no duplicates)",
              db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 6)
        roles = {r["role"] for r in db.execute("SELECT role FROM users")}
        check("all 4 roles present", roles == {"admin", "doctor", "nurse", "receptionist"})
        pw = db.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()[0]
        check("passwords are hashed, not plain text", pw != "Demo@1234" and ":" in pw)

        print("Retrieval and relationships")
        row = db.execute(
            "SELECT p.mrn, COUNT(m.id) AS n FROM patients p "
            "JOIN medical_records m ON m.patient_id = p.id "
            "WHERE p.mrn = 'MRN-0001' GROUP BY p.id").fetchone()
        check("patient MRN-0001 has 2 records via JOIN", row["n"] == 2)
        row = db.execute(
            "SELECT u.username FROM medical_records m JOIN users u ON u.id = m.created_by "
            "WHERE m.diagnosis = 'Hypertension'").fetchone()
        check("record -> creator relationship works", row["username"] == "dr_sharma")

        print("Insert activity log and alert")
        uid = db.execute("SELECT id FROM users WHERE username='nurse_priya'").fetchone()[0]
        cur = db.execute(
            "INSERT INTO activity_logs (user_id, role, action, resource_type, records_accessed) "
            "VALUES (?, 'nurse', 'view_patient', 'patient', 1)", (uid,))
        log_id = cur.lastrowid
        db.execute(
            "INSERT INTO alerts (activity_log_id, user_id, anomaly_score, risk_level, explanation) "
            "VALUES (?, ?, -0.2, 'medium', 'test alert')", (log_id, uid))
        db.commit()
        row = db.execute(
            "SELECT a.status, l.action, u.username FROM alerts a "
            "JOIN activity_logs l ON l.id = a.activity_log_id "
            "JOIN users u ON u.id = a.user_id").fetchone()
        check("alert -> log -> user relationship works",
              row["action"] == "view_patient" and row["username"] == "nurse_priya")
        check("alert status defaults to 'new'", row["status"] == "new")

        print("Constraints")
        expect_error("foreign key rejects record for non-existent patient", lambda: db.execute(
            "INSERT INTO medical_records (patient_id, record_type, created_by) "
            "VALUES (9999, 'x', 1)"))
        expect_error("invalid role rejected", lambda: db.execute(
            "INSERT INTO users (username, password_hash, full_name, role) "
            "VALUES ('x', 'h', 'X', 'hacker')"))
        expect_error("duplicate username rejected", lambda: db.execute(
            "INSERT INTO users (username, password_hash, full_name, role) "
            "VALUES ('admin', 'h', 'X', 'nurse')"))
        expect_error("invalid risk level rejected", lambda: db.execute(
            "INSERT INTO alerts (risk_level) VALUES ('extreme')"))
        db.rollback()

    print("Routes")
    client = app.test_client()
    check("GET / returns 200", client.get("/").status_code == 200)
    r = client.get("/health")
    check("GET /health returns ok", r.status_code == 200 and r.get_json()["status"] == "ok")
    r = client.get("/db-check")
    check("GET /db-check now requires login (redirects to /login)",
          r.status_code == 302 and "/login" in r.headers["Location"])

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nAll {passed} checks passed.")


if __name__ == "__main__":
    main()