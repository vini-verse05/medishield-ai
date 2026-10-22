import re
import sqlite3
from datetime import date, datetime

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)

from audit import log_activity
from database import get_db
from security import has_permission, permission_required

bp = Blueprint("patients", __name__)

MAX_LIST = 50
GENDERS = ["Male", "Female", "Other"]
BLOOD_GROUPS = ["", "A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
PHONE_RE = re.compile(r"^[0-9+\-() ]{5,20}$")

# (column, label, input kind)
FIELD_DEFS = [
    ("first_name", "First name", "text"),
    ("last_name", "Last name", "text"),
    ("date_of_birth", "Date of birth", "date"),
    ("gender", "Gender", "gender"),
    ("phone", "Phone", "text"),
    ("address", "Address", "textarea"),
    ("blood_group", "Blood group", "blood"),
    ("allergies", "Allergies", "text"),
]
DEMOGRAPHIC_FIELDS = ["first_name", "last_name", "date_of_birth", "gender", "phone", "address"]
ALL_FIELDS = DEMOGRAPHIC_FIELDS + ["blood_group", "allergies"]
CONTACT_FIELDS = ["phone", "address"]

RECORD_TYPES = {
    "doctor": ["Consultation", "Lab Report", "Prescription", "Procedure", "Nursing Note"],
    "nurse": ["Nursing Note"],
}


# ------------------------------------------------------------------- helpers
def visible_fields(role):
    """Receptionists see demographics only; clinical roles see everything."""
    return ALL_FIELDS if has_permission(role, "view_patient_details") else DEMOGRAPHIC_FIELDS


def editable_fields(role):
    """Built on the server from the role; submitted form keys never extend it."""
    if has_permission(role, "update_patient"):
        return ALL_FIELDS
    if has_permission(role, "update_patient_contact"):
        return CONTACT_FIELDS
    return []


def field_defs_for(fields):
    return [d for d in FIELD_DEFS if d[0] in fields]


def clean_patient_form(form, fields):
    """Validate only the given fields. Returns (values, errors)."""
    values = {f: form.get(f, "").strip() for f in fields}
    errors = []

    for name, label in (("first_name", "First name"), ("last_name", "Last name")):
        if name in fields and not (1 <= len(values[name]) <= 50):
            errors.append(f"{label} is required (max 50 characters).")

    if "date_of_birth" in fields:
        try:
            dob = datetime.strptime(values["date_of_birth"], "%Y-%m-%d").date()
            if dob > date.today() or dob.year < 1900:
                raise ValueError
            values["date_of_birth"] = dob.isoformat()
        except ValueError:
            errors.append("Date of birth must be a valid past date (YYYY-MM-DD).")

    if "gender" in fields and values["gender"] not in GENDERS:
        errors.append("Please choose a valid gender.")
    if "phone" in fields and values["phone"] and not PHONE_RE.match(values["phone"]):
        errors.append("Phone may contain only digits, + - ( ) and spaces (5-20 characters).")
    if "address" in fields and len(values["address"]) > 200:
        errors.append("Address must be at most 200 characters.")
    if "blood_group" in fields and values["blood_group"] not in BLOOD_GROUPS:
        errors.append("Please choose a valid blood group.")
    if "allergies" in fields and len(values["allergies"]) > 200:
        errors.append("Allergies must be at most 200 characters.")
    return values, errors


def clean_record_form(form, allowed_types):
    values = {k: form.get(k, "").strip()
              for k in ("record_type", "diagnosis", "treatment", "notes")}
    errors = []
    if values["record_type"] not in allowed_types:
        errors.append("That record type is not allowed for your role.")
    if not (1 <= len(values["diagnosis"]) <= 200):
        errors.append("Diagnosis is required (max 200 characters).")
    if len(values["treatment"]) > 500:
        errors.append("Treatment must be at most 500 characters.")
    if len(values["notes"]) > 2000:
        errors.append("Notes must be at most 2000 characters.")
    return values, errors


def get_patient_or_404(patient_id):
    patient = get_db().execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    if patient is None:
        abort(404)
    return patient


def render_patient_form(title, mode, fields_shown, editable, values, static, status=200):
    return render_template(
        "patient_form.html", title=title, mode=mode,
        field_defs=field_defs_for(fields_shown), editable=editable,
        values=values, static=static, genders=GENDERS, blood_groups=BLOOD_GROUPS,
    ), status


# ---------------------------------------------------------------- patient list
@bp.route("/patients")
@permission_required("view_patients")
def list_patients():
    q = request.args.get("q", "").strip()[:50]
    where, params = "", []
    if q:
        like = f"%{q}%"
        where = ("WHERE mrn LIKE ? OR first_name LIKE ? OR last_name LIKE ? "
                 "OR (first_name || ' ' || last_name) LIKE ? OR phone LIKE ?")
        params = [like] * 5

    rows = get_db().execute(
        "SELECT id, mrn, first_name, last_name, date_of_birth, gender, phone "
        f"FROM patients {where} ORDER BY last_name, first_name LIMIT ?",
        params + [MAX_LIST],
    ).fetchall()

    log_activity(
        "search_patients" if q else "list_patients",
        resource_type="patient",
        records_accessed=len(rows),
        details=f"query={q!r}; results={len(rows)}" if q else f"results={len(rows)}",
    )
    return render_template("patients_list.html", patients=rows, q=q, max_list=MAX_LIST)


# --------------------------------------------------------------- patient detail
@bp.route("/patients/<int:patient_id>")
@permission_required("view_patients")
def patient_detail(patient_id):
    patient = get_patient_or_404(patient_id)
    role = g.user["role"]
    show_clinical = has_permission(role, "view_patient_details")
    show_records = has_permission(role, "view_medical_records")

    records = []
    if show_records:
        records = get_db().execute(
            "SELECT m.id, m.record_type, m.diagnosis, m.treatment, m.notes, "
            "m.created_at, m.updated_at, c.full_name AS created_by_name, "
            "u.full_name AS updated_by_name "
            "FROM medical_records m JOIN users c ON c.id = m.created_by "
            "LEFT JOIN users u ON u.id = m.updated_by "
            "WHERE m.patient_id = ? ORDER BY m.created_at DESC, m.id DESC",
            (patient_id,),
        ).fetchall()

    log_activity(
        "view_patient", resource_type="patient", resource_id=patient_id,
        records_accessed=1,
        details=f"medical_records_shown={len(records)}" if show_records else "demographics only",
    )
    return render_template("patient_detail.html", patient=patient, records=records,
                           show_clinical=show_clinical, show_records=show_records)


# -------------------------------------------------------------- register patient
@bp.route("/patients/new", methods=["GET", "POST"])
@permission_required("register_patient")
def register_patient():
    fields = DEMOGRAPHIC_FIELDS
    if request.method == "GET":
        return render_patient_form("Register patient", "register", fields, fields, {}, {})

    values, errors = clean_patient_form(request.form, fields)
    if errors:
        for e in errors:
            flash(e, "danger")
        return render_patient_form("Register patient", "register", fields, fields,
                                   values, {}, 400)

    db = get_db()
    try:
        next_no = db.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM patients").fetchone()[0]
        mrn = f"MRN-{next_no:04d}"
        cur = db.execute(
            "INSERT INTO patients (mrn, first_name, last_name, date_of_birth, gender, "
            "phone, address) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (mrn, values["first_name"], values["last_name"], values["date_of_birth"],
             values["gender"], values["phone"] or None, values["address"] or None),
        )
        patient_id = cur.lastrowid
        log_activity("register_patient", resource_type="patient", resource_id=patient_id,
                     records_accessed=0, details=f"mrn={mrn}")
    except sqlite3.IntegrityError:
        db.rollback()
        flash("Could not register the patient. Please try again.", "danger")
        return render_patient_form("Register patient", "register", fields, fields,
                                   values, {}, 400)

    flash(f"Patient registered with MRN {mrn}.", "success")
    return redirect(url_for("patients.patient_detail", patient_id=patient_id))


# ----------------------------------------------------------------- edit patient
@bp.route("/patients/<int:patient_id>/edit", methods=["GET", "POST"])
@permission_required("update_patient", "update_patient_contact")
def edit_patient(patient_id):
    patient = get_patient_or_404(patient_id)
    role = g.user["role"]
    editable = editable_fields(role)
    shown = visible_fields(role)
    static = dict(patient)
    title = f"Edit patient {patient['mrn']}"

    if request.method == "GET":
        values = {f: patient[f] or "" for f in shown}
        return render_patient_form(title, "edit", shown, editable, values, static)

    values, errors = clean_patient_form(request.form, editable)
    if errors:
        for e in errors:
            flash(e, "danger")
        return render_patient_form(title, "edit", shown, editable, values, static, 400)

    changed = [f for f in editable if (values[f] or None) != patient[f]]
    if not changed:
        flash("No changes were made.", "info")
        return redirect(url_for("patients.patient_detail", patient_id=patient_id))

    # Column names come from the server-side whitelist, never from the request.
    db = get_db()
    set_clause = ", ".join(f"{f} = ?" for f in changed)
    db.execute(
        f"UPDATE patients SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
        [values[f] or None for f in changed] + [patient_id],
    )
    log_activity("update_patient", resource_type="patient", resource_id=patient_id,
                 records_accessed=1, details="fields_changed=" + ",".join(changed))
    flash("Patient updated.", "success")
    return redirect(url_for("patients.patient_detail", patient_id=patient_id))


# ---------------------------------------------------------------- medical records
@bp.route("/patients/<int:patient_id>/records/new", methods=["GET", "POST"])
@permission_required("create_medical_record")
def new_record(patient_id):
    patient = get_patient_or_404(patient_id)
    types = RECORD_TYPES.get(g.user["role"], [])
    title = "New medical record"

    def render(values, status=200):
        return render_template("record_form.html", title=title, patient=patient,
                               types=types, values=values), status

    if request.method == "GET":
        return render({})

    values, errors = clean_record_form(request.form, types)
    if errors:
        for e in errors:
            flash(e, "danger")
        return render(values, 400)

    db = get_db()
    cur = db.execute(
        "INSERT INTO medical_records (patient_id, record_type, diagnosis, treatment, "
        "notes, created_by) VALUES (?, ?, ?, ?, ?, ?)",
        (patient_id, values["record_type"], values["diagnosis"],
         values["treatment"] or None, values["notes"] or None, g.user["id"]),
    )
    log_activity("create_medical_record", resource_type="medical_record",
                 resource_id=cur.lastrowid, records_accessed=0,
                 details=f"patient_id={patient_id}; type={values['record_type']}")
    flash("Medical record added.", "success")
    return redirect(url_for("patients.patient_detail", patient_id=patient_id))


@bp.route("/records/<int:record_id>/edit", methods=["GET", "POST"])
@permission_required("update_medical_record")
def edit_record(record_id):
    db = get_db()
    record = db.execute(
        "SELECT m.id, m.patient_id, m.record_type, m.diagnosis, m.treatment, m.notes, "
        "p.mrn, p.first_name, p.last_name "
        "FROM medical_records m JOIN patients p ON p.id = m.patient_id WHERE m.id = ?",
        (record_id,),
    ).fetchone()
    if record is None:
        abort(404)

    types = RECORD_TYPES.get(g.user["role"], [])
    patient = {"mrn": record["mrn"], "first_name": record["first_name"],
               "last_name": record["last_name"]}
    fields = ["record_type", "diagnosis", "treatment", "notes"]

    def render(values, status=200):
        return render_template("record_form.html", title="Edit medical record",
                               patient=patient, types=types, values=values), status

    if request.method == "GET":
        return render({f: record[f] or "" for f in fields})

    values, errors = clean_record_form(request.form, types)
    if errors:
        for e in errors:
            flash(e, "danger")
        return render(values, 400)

    changed = [f for f in fields if (values[f] or None) != record[f]]
    if not changed:
        flash("No changes were made.", "info")
        return redirect(url_for("patients.patient_detail", patient_id=record["patient_id"]))

    set_clause = ", ".join(f"{f} = ?" for f in changed)
    db.execute(
        f"UPDATE medical_records SET {set_clause}, updated_by = ?, "
        "updated_at = datetime('now') WHERE id = ?",
        [values[f] or None for f in changed] + [g.user["id"], record_id],
    )
    log_activity("update_medical_record", resource_type="medical_record",
                 resource_id=record_id, records_accessed=1,
                 details="fields_changed=" + ",".join(changed))
    flash("Medical record updated.", "success")
    return redirect(url_for("patients.patient_detail", patient_id=record["patient_id"]))