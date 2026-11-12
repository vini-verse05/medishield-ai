# MediShield AI

Intelligent insider-threat detection for a secure Electronic Health Record (EHR)
prototype. A small Flask EHR records every user action in an audit log; per-role
Isolation Forest models look for unusual user-days; administrators review the
resulting risk-scored alerts.

**This is an academic prototype using fictional sample data. It is not a
production healthcare system, and an alert is a warning for investigation, not
proof of malicious intent.**

## Features

- Login/logout, 4 roles (admin, doctor, nurse, receptionist), server-side role-based access control, CSRF protection, hashed passwords.
- Patient search, view, register and edit; medical records with role-specific rules.
- Audit log of logins, failed logins, views, edits, access denials and CSRF failures (written only by server code).
- ML: 10 behavioural features per user-day, one Isolation Forest per role, documented per-role threshold.
- Risk levels (low to critical), signal-based explanations, duplicate-free alerts, admin review.
- Administrator dashboard: summary cards, charts, role comparison, recent alerts and activity.

## Stack

Python, Flask, SQLite, Jinja2 + Bootstrap, pandas, NumPy, scikit-learn, Chart.js (CDN).

## Quick start (Windows)

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python setup_project.py
flask --app app run --debug
```

Open http://127.0.0.1:5000/login. Demo password for every sample user: `Demo@1234`
(users: admin, dr_sharma, dr_mehta, nurse_priya, nurse_rahul, recep_anita).
Set `SECRET_KEY` (and `SHOW_DEMO_CREDENTIALS=0`) in the environment for anything beyond a demo.

## Tests and evaluation

```bat
python run_all_tests.py                 :: all tests, writes docs\TEST_RESULTS.md
python -m ml.evaluate                   :: one evaluation run -> ml\reports\evaluation.md
python -m ml.evaluate_multiseed         :: 5 runs, mean +- std -> ml\reports\evaluation_multiseed.md
```

Evaluation numbers come only from running these commands; none are hard-coded.

## Using the ML pipeline

```bat
flask --app app analyze                 :: score all activity logs and create alerts
python demo_activity.py bulk            :: demo: bulk patient access
python demo_activity.py attack          :: demo: repeated failed logins
```

See `docs/DEMO_GUIDE.md` for step-by-step demonstration scenarios.

## Project layout

app.py config.py database.py schema.sql security.py audit.py analysis.py dashboard*data.py
seed_data.py demo_activity.py setup_project.py run_all_tests.py test*\*.py
routes/ main.py auth.py admin.py patients.py alerts.py
templates/ HTML (Jinja2)
static/ css/style.css js/dashboard.js
ml/ settings.py features.py generate_data.py train.py detector.py evaluate.py
evaluate_multiseed.py explain.py (data/ and models/ are generated)
docs/ ML_DESIGN.md RISK_AND_ALERTS.md DEMO_GUIDE.md LIMITATIONS_AND_FUTURE_WORK.md TEST_RESULTS.md
instance/ medishield.db (generated)

## Verify from a clean environment

```bat
robocopy medishield-ai clean-check /E /XD venv instance data models __pycache__ .git
cd clean-check
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python setup_project.py
python run_all_tests.py
flask --app app run
```

This copy has no database, no data and no model, so everything is rebuilt from the documented steps.

## Documentation

- `docs/ML_DESIGN.md`: dataset choice, features, preprocessing, model, threshold, evaluation protocol.
- `docs/RISK_AND_ALERTS.md`: risk mapping, explanations, duplicate prevention.
- `docs/LIMITATIONS_AND_FUTURE_WORK.md`: honest limits and next steps.

## Claims

The contribution is the integration of EHR access control, audit logging and role-aware
anomaly detection with risk-based alerts. No novelty claim has been checked against the
literature; do that before making one. The model does not prevent insider threats or
guarantee detection.
