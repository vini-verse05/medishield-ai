# MediShield AI: risk scoring and alerts

## Workflow

1. Users act in the app; server code writes rows to `activity_logs`.
2. Analysis (admin button or `flask --app app analyze`) loads all log rows
   except the seeded `SAMPLE DATA` rows.
3. `ml.features.extract_features` builds one feature row per user per local day
   (identical code to what prepares the training data).
4. The role's Isolation Forest scores each row (`anomaly_score`, higher = more
   unusual for that role). A day is flagged if the score exceeds that role's
   threshold (95th percentile of training scores).
5. Every scored day is stored in `analysis_results` (flagged or not).
6. Each flagged day creates or updates one row in `alerts`.
7. An administrator reviews the alert and marks it reviewed.

## Risk levels

position = (score - threshold) / (highest training score for the role - threshold)

| Position       | Base level                                           |
| -------------- | ---------------------------------------------------- |
| < 0.25         | low                                                  |
| 0.25 to < 0.60 | medium                                               |
| 0.60 to < 1.00 | high                                                 |
| >= 1.00        | critical (at or above the most unusual training day) |

Escalation (+1 level, never above critical) when any rule holds:

- 10 or more failed logins in the day
- 10 or more denied requests in the day
- off-hours actions > 0 and records accessed above the role's 95th percentile

The band cut-offs (0.25 / 0.60 / 1.00) and escalation limits (10) are design
choices, not values tuned on data. They are constants in `ml/explain.py`.

## Explanations

Up to 4 signals per alert: measures above the role's training 95th percentile
(and above its median), or a first login before 06:00 / after 22:00. Each signal
shows the observed value, the role's typical value and its 95th percentile.
If no single measure is unusual, the explanation says the combination is unusual
(this includes unusually low activity). Isolation Forest does not report
per-feature contributions, so the signals are a comparison against the role
baseline, not a decomposition of the score.

## Duplicate prevention

Unique index on `alerts(user_id, alert_date)` plus application logic:
create if missing; update in place if status is `new`; never touch if `reviewed`.

## Limitations

- The model was trained on synthetic data. The demo app has 8 patients and a few
  users, so its volumes differ from the training data. Low-activity days can be
  flagged because they are unlike the synthetic baseline.
- Analysis is on demand, and a day still in progress is analysed as a partial day.
- Alerts are not removed when a later run no longer flags the day.
- A reviewed alert is not reopened even if the day later becomes more unusual.
- A flag means "worth investigating", not misuse.
