# Limitations and future work

## Data and evaluation

- Training and evaluation data are **synthetic**, and both the normal profiles and the attack scenarios were written by the author. Results measure how well the model finds the assumed patterns, not how it would behave in a hospital.
- The test anomaly rate is inflated for evaluation, so single-run precision is optimistic. FPR is the more transferable figure; `evaluation_multiseed.md` shows a derived projection for lower prevalence.
- The demo app has 8 patients and a handful of users, so its activity volumes differ from the synthetic data; quiet real days can be flagged.
- No public labelled EHR audit dataset was used. The CERT Insider Threat dataset was assessed and not adopted because it contains no patient-record events.
- No literature comparison or novelty check has been done.

## Model

- Unsupervised Isolation Forest: "unusual" is not "malicious". Emergencies, new staff and shift changes will be flagged.
- Baselines are per role, not per user. The model assumes the training period is mostly normal.
- Daily aggregation delays detection; a day in progress is scored as a partial day.
- Explanations compare feature values with the role baseline; they are not a decomposition of the Isolation Forest score.
- Risk band cut-offs and escalation limits are design choices, not tuned values.
- The model is not retrained automatically. The timezone offset (330 minutes, IST) is set in `ml/settings.py`.

## Application and security

- Any doctor or nurse can open any patient (no care-team assignment, no break-glass workflow).
- Audit-log immutability is enforced by application design only, not by database triggers or external write-once storage.
- No account lockout, rate limiting, password policy or multi-factor authentication.
- No Content-Security-Policy header (Bootstrap and Chart.js load from CDNs). Charts need internet access.
- Sample data only. Demo credentials are for local demonstration and the login-page hint can be switched off.
- Analysis is on demand (button or `flask --app app analyze`); there is no scheduler.
- Alerts are never auto-closed, and reviewed alerts are not reopened.
- Dashboard views are not audit-logged (deliberate, so they do not affect the ML features).

## Future work

1. Retrain on accumulated real application logs and compare with the synthetic baseline.
2. Per-user baselines plus role baselines; weekday/weekend and department features.
3. Care-team assignment checks and a documented break-glass flow.
4. Scheduled analysis, e-mail or webhook notification, alert triage notes.
5. SHAP-style or permutation-based per-feature explanations.
6. External validation on a mapped public dataset (for example CERT).
7. Account lockout, rate limiting, MFA, tamper-evident (hash-chained) audit logs.
8. A literature review to position the contribution.
