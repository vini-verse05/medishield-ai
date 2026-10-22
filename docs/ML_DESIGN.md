# MediShield AI: ML design notes (Part 4)

## 1. Dataset choice

- **CERT Insider Threat Test Dataset (CMU SEI)**: well-known synthetic insider-threat
  dataset with labelled scenarios. Its logs are enterprise IT events (logon, device,
  file, email, web). It has no patient records or clinical roles and is large, so it
  does not match this application. (Described from general knowledge; verify details
  at the source before citing.)
- No labelled public EHR audit logs are known to the author (privacy constraints).
- **Chosen:** synthetic healthcare activity logs (`ml/generate_data.py`) with the same
  columns as the application's `activity_logs` table.
- Future work: map CERT events onto these features for external validation.

## 2. Synthetic data construction

- 32 synthetic users (2 admin, 10 doctors, 14 nurses, 6 receptionists), 120 days,
  fixed random seed (reproducible).
- Normal behaviour per role: shift times, Poisson-distributed daily counts of patient
  views, searches, list requests, updates and record creations, an occasional typo'd
  login, weekday/weekend work probability.
- Anomalies replace a user-day with one of six scenarios: `bulk_access`,
  `off_hours_access`, `credential_attack`, `privilege_probing`, `mass_modification`,
  `subtle_snooping`. Not every scenario applies to every role (admins never access
  patients).
- Injection rate per user-day: 3% in the training period, 8% in the test period.
- Labels are stored separately and are used ONLY for evaluation.
- The scenarios and the normal profiles were written by the author, so evaluation
  results measure how well the model finds the patterns assumed here, not how it
  would behave on real hospital data.

## 3. Features (per user, per local day)

See the table in `ml/features.py`: login_hour, off_hours_actions, failed_logins,
records_accessed, patients_viewed, list_search_actions, write_actions,
denied_actions, total_events, peak_hourly_events.

## 4. Preprocessing

- UTC timestamps -> local time (`TIMEZONE_OFFSET_MINUTES`, default 330 = IST).
- Off hours = 22:00-05:59 local.
- Rows without a user or role (unknown-username failed logins) are excluded.
- Missing login (days with only failed logins) -> hour of the first event.
- No scaling (tree-based model). Features are cast to float.

## 5. Model, score and threshold

- One `IsolationForest` (200 trees) per role; training is unsupervised on the first
  90 days, which include ~3% unlabelled anomalies.
- `anomaly_score = -score_samples(X)`: higher = more unusual for that role. Scores of
  different roles are not directly comparable.
- Threshold per role = 95th percentile of that role's training scores
  (`TARGET_FLAG_RATE = 0.05`). A day is flagged when its score is strictly above it.
- By construction, about 5% of normal training days are flagged, so a quiet system will
  still produce some flags. The flag rate is a policy choice, not a measured optimum.

## 6. Evaluation protocol

- Chronological split: last 30 days are test data.
- Metrics: confusion matrix, precision, recall, F1, false-positive rate, ROC-AUC and
  average precision (pooled using score minus role threshold), per-role and
  per-scenario results.
- Baseline: a single global model without role information.
- Sensitivity table across flag rates (informational only).
- The test anomaly rate is inflated, so precision is optimistic compared with a real
  deployment with rarer anomalies. FPR does not depend on prevalence.
- Results are written by `python -m ml.evaluate` to `ml/reports/`.

## 7. Limitations

- Synthetic data: results are not evidence of real-world performance.
- Unsupervised: "unusual" is not "malicious". Legitimate emergencies, new staff and
  shift changes will be flagged.
- The model assumes the training period is mostly normal. A malicious insider active
  throughout training would be learned as normal.
- Role-level baselines only; no per-user personal baselines.
- Daily aggregation delays detection until enough of the day has been logged.
- No weekday/weekend or department features.
- The synthetic data's patient-count scale differs from the demo app (8 patients), so
  the model should be retrained on real application logs once enough accumulate.
- Isolation Forest scores are not probabilities.
