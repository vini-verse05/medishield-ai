"""Part 3 verification: EHR workflow, role restrictions and audit logging.
Uses a temporary database; your real instance/medishield.db is untouched.

Run:  python test_ehr.py
"""
import glob
import os
import shutil
import tempfile

from app import create_app
from database import get_db, init_db
from seed_data import DEMO_PASSWORD, seed_database

passed = 0
ROW = b'class="patient-row"'
LOGROW = b'class="log-row"'


def check(name, condition):
    global passed
    if not condition:
        raise AssertionError(f"FAILED: {name}")
    passed += 1
    print(f"  [PASS] {name}")


# ------------------------------------------------------------------- helpers
def build_app(tmp):
    app = create_app({"DATABASE": os.path.join(tmp, "test.db"),
                      "TESTING": True, "SECRET_KEY": "test-secret"})
    with app.app_context():
        init_db()
        seed_database(get_db())
    return app


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
    r = login(c, username)
    assert r.status_code == 302, f"login failed for {username}"
    return c


def rows(app, sql, params=()):
    with app.app_context():
        return [dict(r) for r in get_db().execute(sql, params).fetchall()]


def val(app, sql, params=()):
    with app.app_context():
        return get_db().execute(sql, params).fetchone()[0]


def mark(app):
    return val(app, "SELECT COALESCE(MAX(id), 0) FROM activity_logs")


def logs_since(app, m):
    return rows(app, "SELECT * FROM activity_logs WHERE id > ? ORDER BY id", (m,))


def uid(app, username):
    return val(app, "SELECT id FROM users WHERE username = ?", (username,))


PATIENT_FORM = {
    "first_name": "Aarav", "last_name": "Patel", "date_of_birth": "1985-03-14",
    "gender": "Male", "phone": "555-0101", "address": "12 Sample Street",
    "blood_group": "B+", "allergies": "Penicillin",
}


def main():
    tmp = tempfile.mkdtemp()
    app = build_app(tmp)
    pid = val(app, "SELECT id FROM patients WHERE mrn = 'MRN-0001'")

    # ------------------------------------------------------------ auth logging
    print("Authentication events are logged")
    c = app.test_client()
    m = mark(app)
    login(c, "nurse_priya", "wrong-password")
    L = logs_since(app, m)
    check("wrong password creates exactly one log row", len(L) == 1)
    check("it is a failed login for the known user",
          L[0]["action"] == "login_failed" and L[0]["success"] == 0
          and L[0]["username_attempted"] == "nurse_priya" and L[0]["user_id"] is not None
          and L[0]["details"] == "wrong password")
    m = mark(app)
    login(c, "ghost_user", "x")
    L = logs_since(app, m)
    check("unknown username is logged with NULL user_id",
          len(L) == 1 and L[0]["action"] == "login_failed" and L[0]["user_id"] is None
          and L[0]["username_attempted"] == "ghost_user")
    m = mark(app)
    login(c, "dr_sharma")
    L = logs_since(app, m)
    check("successful login is logged with role",
          len(L) == 1 and L[0]["action"] == "login" and L[0]["success"] == 1
          and L[0]["role"] == "doctor")
    m = mark(app)
    post(c, "/logout")
    L = logs_since(app, m)
    check("logout is logged", len(L) == 1 and L[0]["action"] == "logout")

    # ------------------------------------------------------------ patient list
    print("Patient list and search")
    doctor = client_for(app, "dr_sharma")
    m = mark(app)
    r = doctor.get("/patients")
    L = logs_since(app, m)
    check("doctor sees all 8 patients", r.status_code == 200 and r.data.count(ROW) == 8)
    check("list is logged with records_accessed = 8",
          len(L) == 1 and L[0]["action"] == "list_patients" and L[0]["records_accessed"] == 8
          and L[0]["role"] == "doctor")
    m = mark(app)
    r = doctor.get("/patients?q=Patel")
    L = logs_since(app, m)
    check("search finds 1 patient", r.data.count(ROW) == 1 and b"MRN-0001" in r.data)
    check("search is logged as search_patients with 1 record",
          L[0]["action"] == "search_patients" and L[0]["records_accessed"] == 1)
    r = doctor.get("/patients", query_string={"q": "' OR 1=1 --"})
    check("SQL-injection style search is harmless (0 rows, no error)",
          r.status_code == 200 and r.data.count(ROW) == 0)
    for username in ["nurse_priya", "recep_anita"]:
        check(f"{username} can list patients",
              client_for(app, username).get("/patients").status_code == 200)

    admin = client_for(app, "admin")
    m = mark(app)
    r = admin.get("/patients")
    L = logs_since(app, m)
    check("admin cannot list patients (403)", r.status_code == 403)
    check("denied access is logged as access_denied",
          len(L) == 1 and L[0]["action"] == "access_denied" and L[0]["success"] == 0
          and L[0]["role"] == "admin" and "/patients" in L[0]["details"])
    anon = app.test_client()
    check("anonymous cannot list patients", anon.get("/patients").status_code == 302)
    check("anonymous cannot open a patient", anon.get(f"/patients/{pid}").status_code == 302)

    # ------------------------------------------------------------ patient detail
    print("Patient detail and role-based visibility")
    m = mark(app)
    r = doctor.get(f"/patients/{pid}")
    L = logs_since(app, m)
    check("doctor sees allergies and diagnosis",
          r.status_code == 200 and b"Penicillin" in r.data and b"Hypertension" in r.data)
    check("view is logged with resource id and 2 records shown",
          L[0]["action"] == "view_patient" and L[0]["resource_id"] == pid
          and L[0]["records_accessed"] == 1 and "medical_records_shown=2" in L[0]["details"])
    nurse = client_for(app, "nurse_priya")
    r = nurse.get(f"/patients/{pid}")
    check("nurse sees clinical data", r.status_code == 200 and b"Hypertension" in r.data)
    recep = client_for(app, "recep_anita")
    m = mark(app)
    r = recep.get(f"/patients/{pid}")
    L = logs_since(app, m)
    check("receptionist sees demographics", r.status_code == 200 and b"Aarav" in r.data)
    check("receptionist does NOT see allergies or diagnoses",
          b"Penicillin" not in r.data and b"Hypertension" not in r.data)
    check("receptionist view is logged as demographics only",
          L[0]["action"] == "view_patient" and L[0]["details"] == "demographics only")
    check("admin cannot open a patient (403)", admin.get(f"/patients/{pid}").status_code == 403)
    check("missing patient returns 404", doctor.get("/patients/9999").status_code == 404)

    # ------------------------------------------------------------ update patient
    print("Updating patients")
    form = dict(PATIENT_FORM, phone="555-7777", allergies="Latex")
    m = mark(app)
    r = post(doctor, f"/patients/{pid}/edit", form)
    L = logs_since(app, m)
    check("doctor update succeeds", r.status_code == 302)
    check("database row changed",
          val(app, "SELECT phone FROM patients WHERE id=?", (pid,)) == "555-7777"
          and val(app, "SELECT allergies FROM patients WHERE id=?", (pid,)) == "Latex")
    check("updated_at is set", val(app, "SELECT updated_at FROM patients WHERE id=?", (pid,)) is not None)
    check("update logged with changed field names only",
          L[0]["action"] == "update_patient" and "phone" in L[0]["details"]
          and "allergies" in L[0]["details"] and "first_name" not in L[0]["details"]
          and "555-7777" not in L[0]["details"] and "Latex" not in L[0]["details"])
    check("update counts as 1 record accessed", L[0]["records_accessed"] == 1)

    r = post(doctor, f"/patients/{pid}/edit", dict(form, gender="X", phone="555-0000"))
    check("invalid gender rejected (400) and nothing saved",
          r.status_code == 400 and val(app, "SELECT phone FROM patients WHERE id=?", (pid,)) == "555-7777")
    r = post(doctor, f"/patients/{pid}/edit", dict(form, date_of_birth="2999-01-01"))
    check("future date of birth rejected (400)", r.status_code == 400)

    m = mark(app)
    r = post(nurse, f"/patients/{pid}/edit", dict(form, phone="555-1111"))
    L = logs_since(app, m)
    check("nurse cannot update a patient (403)", r.status_code == 403)
    check("nurse change was not saved",
          val(app, "SELECT phone FROM patients WHERE id=?", (pid,)) == "555-7777")
    check("blocked update is logged", L[0]["action"] == "access_denied")
    check("nurse cannot open the edit page", nurse.get(f"/patients/{pid}/edit").status_code == 403)

    tampered = {"phone": "555-1234", "address": "99 New Road", "first_name": "HACKED",
                "allergies": "Nothing", "blood_group": "A+"}
    m = mark(app)
    r = post(recep, f"/patients/{pid}/edit", tampered)
    L = logs_since(app, m)
    check("receptionist contact update succeeds", r.status_code == 302)
    check("phone and address changed",
          val(app, "SELECT phone FROM patients WHERE id=?", (pid,)) == "555-1234"
          and val(app, "SELECT address FROM patients WHERE id=?", (pid,)) == "99 New Road")
    check("tampered fields (name, allergies, blood group) were ignored",
          val(app, "SELECT first_name FROM patients WHERE id=?", (pid,)) == "Aarav"
          and val(app, "SELECT allergies FROM patients WHERE id=?", (pid,)) == "Latex"
          and val(app, "SELECT blood_group FROM patients WHERE id=?", (pid,)) == "B+")
    check("only phone,address logged as changed",
          L[0]["details"] == "fields_changed=phone,address")
    r = recep.get(f"/patients/{pid}/edit")
    check("receptionist edit page does not reveal allergies",
          r.status_code == 200 and b"Latex" not in r.data)

    m = mark(app)
    r = doctor.post(f"/patients/{pid}/edit", data=dict(form, phone="555-4444"))
    L = logs_since(app, m)
    check("update without CSRF token rejected (400)", r.status_code == 400)
    check("CSRF failure logged",
          any(l["action"] == "csrf_failure" and l["success"] == 0 for l in L))
    check("CSRF-rejected change not saved",
          val(app, "SELECT phone FROM patients WHERE id=?", (pid,)) == "555-1234")

    # ------------------------------------------------------------ register
    print("Registering patients")
    count_before = val(app, "SELECT COUNT(*) FROM patients")
    new_form = {"first_name": "Tester", "last_name": "Registered", "date_of_birth": "2000-02-02",
                "gender": "Female", "phone": "555-0199", "address": "1 Test Road",
                "blood_group": "A+", "allergies": "Dust"}
    m = mark(app)
    r = post(recep, "/patients/new", new_form)
    L = logs_since(app, m)
    check("receptionist can register a patient", r.status_code == 302)
    row = rows(app, "SELECT * FROM patients WHERE first_name='Tester'")[0]
    check("patient saved with generated MRN",
          row["mrn"].startswith("MRN-") and val(app, "SELECT COUNT(*) FROM patients") == count_before + 1)
    check("clinical fields were not accepted at registration",
          row["blood_group"] is None and row["allergies"] is None)
    check("registration logged with the new patient id",
          L[0]["action"] == "register_patient" and L[0]["resource_id"] == row["id"]
          and L[0]["records_accessed"] == 0)
    check("doctor cannot open registration page", doctor.get("/patients/new").status_code == 403)
    check("nurse cannot register (403)", post(nurse, "/patients/new", new_form).status_code == 403)
    r = post(recep, "/patients/new", dict(new_form, first_name=""))
    check("missing first name rejected (400)",
          r.status_code == 400 and val(app, "SELECT COUNT(*) FROM patients") == count_before + 1)

    # ------------------------------------------------------------ records
    print("Medical records")
    rec_before = val(app, "SELECT COUNT(*) FROM medical_records")
    note = {"record_type": "Nursing Note", "diagnosis": "Vitals stable",
            "treatment": "Observation", "notes": "SAMPLE note"}
    m = mark(app)
    r = post(nurse, f"/patients/{pid}/records/new", note)
    L = logs_since(app, m)
    check("nurse can add a Nursing Note", r.status_code == 302
          and val(app, "SELECT COUNT(*) FROM medical_records") == rec_before + 1)
    check("record creator is the nurse",
          val(app, "SELECT created_by FROM medical_records ORDER BY id DESC LIMIT 1")
          == uid(app, "nurse_priya"))
    check("record creation logged",
          L[0]["action"] == "create_medical_record" and L[0]["role"] == "nurse")
    r = post(nurse, f"/patients/{pid}/records/new", dict(note, record_type="Consultation"))
    check("nurse cannot create a Consultation (400)",
          r.status_code == 400 and val(app, "SELECT COUNT(*) FROM medical_records") == rec_before + 1)
    r = post(doctor, f"/patients/{pid}/records/new",
             dict(note, record_type="Consultation", notes="<script>alert(1)</script>"))
    check("doctor can add a Consultation", r.status_code == 302)
    page = doctor.get(f"/patients/{pid}").data
    check("script in notes is HTML-escaped (no XSS)",
          b"<script>alert(1)</script>" not in page and b"&lt;script&gt;" in page)
    check("receptionist cannot add records (403)",
          post(recep, f"/patients/{pid}/records/new", note).status_code == 403)

    rid = val(app, "SELECT id FROM medical_records WHERE diagnosis='Hypertension'")
    edit = {"record_type": "Consultation", "diagnosis": "Hypertension (reviewed)",
            "treatment": "Amlodipine 5mg daily", "notes": "SAMPLE: BP monitoring advised."}
    r = post(nurse, f"/records/{rid}/edit", edit)
    check("nurse cannot edit a record (403) and it is unchanged",
          r.status_code == 403
          and val(app, "SELECT diagnosis FROM medical_records WHERE id=?", (rid,)) == "Hypertension")
    m = mark(app)
    r = post(doctor, f"/records/{rid}/edit", edit)
    L = logs_since(app, m)
    check("doctor can edit a record", r.status_code == 302
          and val(app, "SELECT diagnosis FROM medical_records WHERE id=?", (rid,)) == "Hypertension (reviewed)")
    check("editor and timestamp are stored",
          val(app, "SELECT updated_by FROM medical_records WHERE id=?", (rid,)) == uid(app, "dr_sharma")
          and val(app, "SELECT updated_at FROM medical_records WHERE id=?", (rid,)) is not None)
    check("record edit logged with field name only",
          L[0]["action"] == "update_medical_record" and L[0]["details"] == "fields_changed=diagnosis")
    check("invalid record type rejected (400)",
          post(doctor, f"/records/{rid}/edit", dict(edit, record_type="Hacking")).status_code == 400)
    check("missing record returns 404", doctor.get("/records/9999/edit").status_code == 404)

    # ------------------------------------------------------------ audit page
    print("Audit log page")
    m = mark(app)
    r = admin.get("/admin/audit-logs")
    L = logs_since(app, m)
    check("admin can open the audit log", r.status_code == 200 and b"login_failed" in r.data)
    check("opening the audit log is itself logged", L[0]["action"] == "view_audit_logs")
    check("doctor cannot open the audit log (403)", doctor.get("/admin/audit-logs").status_code == 403)
    check("anonymous cannot open the audit log", anon.get("/admin/audit-logs").status_code == 302)
    r = admin.get("/admin/audit-logs?status=failed")
    expected = min(val(app, "SELECT COUNT(*) FROM activity_logs WHERE success = 0"), 50)
    check("'failed' filter shows exactly the failed rows", r.data.count(LOGROW) == expected)
    r = admin.get("/admin/audit-logs?user=nurse_priya")
    check("username filter works", r.status_code == 200 and r.data.count(LOGROW) >= 1)

    print("Logs cannot be altered through the application")
    check("POST to audit page not allowed (405)", post(admin, "/admin/audit-logs").status_code == 405)
    check("no delete route exists (404)", post(admin, "/admin/audit-logs/1/delete").status_code == 404)
    offenders = []
    for path in glob.glob("routes/*.py") + ["security.py", "audit.py", "app.py"]:
        with open(path, encoding="utf-8") as f:
            src = f.read().upper()
        if "DELETE FROM ACTIVITY_LOGS" in src or "UPDATE ACTIVITY_LOGS" in src:
            offenders.append(path)
    check("no application code updates or deletes activity_logs", offenders == [])
    leaked = rows(app, "SELECT id FROM activity_logs WHERE details LIKE '%555-%' "
                       "OR details LIKE '%Latex%' OR details LIKE '%Amlodipine%'")
    check("no patient data values leaked into log details", leaked == [])

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nAll {passed} checks passed.")


if __name__ == "__main__":
    main()