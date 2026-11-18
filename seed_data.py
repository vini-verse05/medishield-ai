"""Sample data for development and demos. ALL DATA IS FICTIONAL.

Every sample user has the demo password below. Never use these credentials
anywhere real.
"""
from werkzeug.security import generate_password_hash

DEMO_PASSWORD = "Demo@1234"

USERS = [
    # username, full_name, role, department
    ("admin",        "Alex Admin",        "admin",        "IT Security"),
    ("dr_sharma",    "Dr. Neha Sharma",   "doctor",       "Cardiology"),
    ("dr_mehta",     "Dr. Rohan Mehta",   "doctor",       "General Medicine"),
    ("nurse_priya",  "Priya Nair",        "nurse",        "General Medicine"),
    ("nurse_rahul",  "Rahul Desai",       "nurse",        "Cardiology"),
    ("recep_anita",  "Anita Kulkarni",    "receptionist", "Front Desk"),
]

PATIENTS = [
    # mrn, first, last, dob, gender, phone, address, blood, allergies
    ("MRN-0001", "Aarav",  "Patel",   "1985-03-14", "Male",   "555-0101", "12 Sample Street",  "B+",  "Penicillin"),
    ("MRN-0002", "Diya",   "Singh",   "1992-07-22", "Female", "555-0102", "34 Example Road",   "O+",  "None"),
    ("MRN-0003", "Kabir",  "Joshi",   "1978-11-02", "Male",   "555-0103", "56 Demo Avenue",    "A+",  "Sulfa drugs"),
    ("MRN-0004", "Isha",   "Reddy",   "2001-01-30", "Female", "555-0104", "78 Test Lane",      "AB+", "None"),
    ("MRN-0005", "Vivaan", "Gupta",   "1965-09-09", "Male",   "555-0105", "90 Sample Colony",  "O-",  "Latex"),
    ("MRN-0006", "Anaya",  "Iyer",    "1999-05-18", "Female", "555-0106", "21 Fiction Nagar",  "A-",  "None"),
    ("MRN-0007", "Reyansh","Kapoor",  "1950-12-25", "Male",   "555-0107", "43 Mock Society",   "B-",  "Aspirin"),
    ("MRN-0008", "Meera",  "Bose",    "1988-08-08", "Female", "555-0108", "65 Placeholder Rd", "O+",  "None"),
]

# mrn, record_type, diagnosis, treatment, notes, created_by_username
RECORDS = [
    ("MRN-0001", "Consultation", "Hypertension",        "Amlodipine 5mg daily",   "SAMPLE: BP monitoring advised.",   "dr_sharma"),
    ("MRN-0001", "Lab Report",   "Lipid profile",       "Diet modification",      "SAMPLE: LDL slightly elevated.",   "dr_sharma"),
    ("MRN-0002", "Consultation", "Seasonal allergy",    "Antihistamine",          "SAMPLE: Follow up in 2 weeks.",    "dr_mehta"),
    ("MRN-0003", "Consultation", "Type 2 diabetes",     "Metformin 500mg",        "SAMPLE: HbA1c review due.",        "dr_mehta"),
    ("MRN-0004", "Consultation", "Migraine",            "Rest, hydration",        "SAMPLE: Trigger diary suggested.", "dr_mehta"),
    ("MRN-0005", "Consultation", "Coronary artery disease", "Statin therapy",     "SAMPLE: Cardiology follow-up.",    "dr_sharma"),
    ("MRN-0005", "Nursing Note", "Vitals stable",       "Routine monitoring",     "SAMPLE: BP 128/82, pulse 74.",     "nurse_rahul"),
    ("MRN-0006", "Consultation", "Viral fever",         "Paracetamol, fluids",    "SAMPLE: Recovered.",               "dr_mehta"),
    ("MRN-0007", "Consultation", "Arrhythmia",          "Beta blocker",           "SAMPLE: ECG scheduled.",           "dr_sharma"),
    ("MRN-0007", "Nursing Note", "Post-ECG observation","Observation 30 min",     "SAMPLE: No adverse events.",       "nurse_rahul"),
    ("MRN-0008", "Consultation", "Routine checkup",     "None required",          "SAMPLE: All parameters normal.",   "dr_mehta"),
    ("MRN-0003", "Nursing Note", "Glucose check",       "Fasting glucose logged", "SAMPLE: 118 mg/dL.",               "nurse_priya"),
]


def seed_database(db):
    """Insert sample data. Skips entirely if any users already exist."""
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
        return "Database already contains users; seeding skipped."

    pw_hash = generate_password_hash(DEMO_PASSWORD)

    db.executemany(
        "INSERT INTO users (username, password_hash, full_name, role, department) "
        "VALUES (?, ?, ?, ?, ?)",
        [(u, pw_hash, name, role, dept) for (u, name, role, dept) in USERS],
    )

    db.executemany(
        "INSERT INTO patients (mrn, first_name, last_name, date_of_birth, gender, "
        "phone, address, blood_group, allergies) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        PATIENTS,
    )

    user_ids = {r["username"]: r["id"] for r in db.execute("SELECT id, username FROM users")}
    patient_ids = {r["mrn"]: r["id"] for r in db.execute("SELECT id, mrn FROM patients")}

    db.executemany(
        "INSERT INTO medical_records (patient_id, record_type, diagnosis, treatment, "
        "notes, created_by) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (patient_ids[mrn], rtype, diag, treat, notes, user_ids[creator])
            for (mrn, rtype, diag, treat, notes, creator) in RECORDS
        ],
    )

    # A few placeholder activity-log rows so the table is not empty at first.
    # They are marked "SAMPLE DATA" and ignored by analysis. Real log entries are
    # written by the app as people use it.
    db.executemany(
        "INSERT INTO activity_logs (user_id, username_attempted, role, action, "
        "resource_type, resource_id, records_accessed, success, ip_address, details) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (user_ids["dr_sharma"], "dr_sharma", "doctor", "login", None, None, 0, 1,
             "127.0.0.1", "SAMPLE DATA"),
            (user_ids["dr_sharma"], "dr_sharma", "doctor", "view_patient", "patient",
             patient_ids["MRN-0001"], 1, 1, "127.0.0.1", "SAMPLE DATA"),
            (None, "unknown_user", None, "login_failed", None, None, 0, 0,
             "127.0.0.1", "SAMPLE DATA"),
        ],
    )

    db.commit()
    return (
        f"Seeded {len(USERS)} users, {len(PATIENTS)} patients, "
        f"{len(RECORDS)} medical records, 3 activity logs. "
        f"Demo password for all users: {DEMO_PASSWORD}"
    )