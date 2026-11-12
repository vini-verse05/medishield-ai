# MediShield AI: demonstration guide

Preparation (once): `python setup_project.py`, then `flask --app app run --debug`.
All demo users share the password `Demo@1234`. Start from a clean slate at any
time with `python setup_project.py --reset-db`.

## Scenario 1: normal clinical workflow

1. Log in as `dr_sharma`. Open **Patients**, search "Patel", open MRN-0001.
2. Click **Edit**, change the phone number, save. Click **Add record** and add a Consultation.
3. Log out. Log in as `nurse_priya`: open a patient. There is no **Edit** button, and **Add record** offers only "Nursing Note".
4. Log out. Log in as `recep_anita`: the patient page shows demographics only (no allergies or records). **Edit** allows phone and address only.
   Expected: each role sees only what its permissions allow.

## Scenario 2: access control cannot be bypassed by URL

1. Logged in as `nurse_priya`, type `/patients/1/edit` and `/admin/alerts` in the address bar.
2. Expected: "403 Access denied" for both.
3. Log in as `admin`, open **Audit logs**, filter Result = "Failed / denied": the two denied requests appear.

## Scenario 3: credential attack

1. In a terminal: `python demo_activity.py attack`, then `flask --app app analyze`.
2. Log in as `admin`: the dashboard and **Alerts** now show an alert for `recep_anita`.
3. Open it: explanation lists the failed-login signal; the risk level is raised because of 10 or more failed logins.

## Scenario 4: bulk access

1. `python demo_activity.py bulk`, then `flask --app app analyze` (or **Run analysis now**).
2. Expected: an alert for `nurse_priya` listing volume signals (records accessed, list/search requests, actions per hour).
3. Open the alert: see the behaviour measures and that day's activity. Click **Mark as reviewed**.

## Scenario 5: no duplicates, reviewed alerts stay reviewed

1. Click **Run analysis now** again.
2. Expected: no second alert for the same user and day; the reviewed alert stays reviewed.

## Scenario 6: dashboard and evaluation

1. Open the administrator **Dashboard**: cards, activity chart, flagged-days chart, alerts by risk, role table.
2. Show the measured evaluation: `python -m ml.evaluate` and `python -m ml.evaluate_multiseed`, then open `ml/reports/evaluation_multiseed.md`.

## Things to say honestly during the demo

- Other quiet users may also be flagged: the model learned "normal" from synthetic days with many more events than this 8-patient demo produces.
- A flag means "worth investigating", not "malicious".
- Evaluation figures come from synthetic data written by the author.
