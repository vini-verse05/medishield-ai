"""Generates SYNTHETIC healthcare activity logs with labelled anomalous days.

Raw events use the same columns as the app's activity_logs table, so the
identical feature code runs on synthetic and real logs. No real data is used.

Run:  python -m ml.generate_data
"""
import argparse
import json
import os
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from ml import settings

PATIENT_POOL = 500
TS_FORMAT = "%Y-%m-%d %H:%M:%S"

# Normal behaviour per role. Counts are daily Poisson means; shifts are
# (mean login hour, standard deviation) in local time.
PROFILES = {
    "admin": {"users": 10, "weekday_p": 0.90, "weekend_p": 0.10, "shifts": [(10.0, 1.2)],
              "views": 0, "searches": 0, "lists": 0, "updates": 0, "creates": 0, "audits": 5},
    "doctor": {"users": 30, "weekday_p": 0.92, "weekend_p": 0.15, "shifts": [(9.0, 1.0)],
               "views": 14, "searches": 5, "lists": 2, "updates": 4, "creates": 3, "audits": 0},
    "nurse": {"users": 40, "weekday_p": 0.90, "weekend_p": 0.35, "shifts": [(7.0, 0.7), (14.0, 0.7)],
              "views": 20, "searches": 6, "lists": 3, "updates": 0, "creates": 6, "audits": 0},
    "receptionist": {"users": 20, "weekday_p": 0.92, "weekend_p": 0.20, "shifts": [(8.5, 0.8)],
                     "views": 16, "searches": 12, "lists": 4, "updates": 5, "creates": 4, "audits": 0},
}

CLINICAL_SCENARIOS = ["bulk_access", "off_hours_access", "credential_attack",
                      "privilege_probing", "mass_modification", "subtle_snooping"]
SCENARIOS = {
    "admin": ["off_hours_access", "credential_attack", "privilege_probing"],
    "doctor": CLINICAL_SCENARIOS,
    "nurse": CLINICAL_SCENARIOS,
    "receptionist": CLINICAL_SCENARIOS,
}


def normal_params(rng, role):
    p = PROFILES[role]
    mean, sd = p["shifts"][int(rng.integers(len(p["shifts"])))]
    return {
        "login_hour": float(np.clip(rng.normal(mean, sd), 5.0, 17.0)),
        "session_hours": float(rng.uniform(6.5, 8.5)),
        "cap_hour": 21.99,
        "logged_in": True,
        "n_failed": int(rng.poisson(0.08)),
        "fail_window_s": 120.0,
        "n_views": int(rng.poisson(p["views"])),
        "n_searches": int(rng.poisson(p["searches"])),
        "n_lists": int(rng.poisson(p["lists"])),
        "n_updates": int(rng.poisson(p["updates"])),
        "n_creates": int(rng.poisson(p["creates"])),
        "n_denied": 0,
        "n_audits": int(rng.poisson(p["audits"])),
    }


def apply_scenario(rng, role, scenario, params):
    """Modify normal parameters to represent one kind of suspicious day."""
    p = PROFILES[role]
    count_keys = ("n_views", "n_searches", "n_lists", "n_updates", "n_creates", "n_audits")

    if scenario == "bulk_access":
        params["n_views"] = int(max(p["views"], 1) * rng.uniform(4, 8))
        params["n_searches"] = int(max(p["searches"], 1) * rng.uniform(2, 4))
        params["n_lists"] += int(rng.integers(5, 15))

    elif scenario == "off_hours_access":
        hour = rng.uniform(0.5, 5.0) if rng.random() < 0.7 else rng.uniform(22.2, 23.3)
        params["login_hour"] = float(hour)
        params["session_hours"] = float(rng.uniform(1, 3))
        params["cap_hour"] = 23.99
        scale = rng.uniform(1.0, 2.5)
        for key in count_keys:
            params[key] = int(params[key] * scale)

    elif scenario == "credential_attack":
        params["n_failed"] = int(rng.integers(8, 31))
        params["fail_window_s"] = 2700.0
        params["login_hour"] = float(rng.uniform(1.0, 20.0))
        if rng.random() < 0.6:
            params["logged_in"] = False  # the attack never got in
        else:
            for key in count_keys:
                params[key] = int(params[key] * 0.5)

    elif scenario == "privilege_probing":
        params["n_denied"] = int(rng.integers(6, 21))

    elif scenario == "mass_modification":
        if role == "nurse":
            params["n_creates"] = int(rng.integers(30, 60))
        elif role == "doctor":
            params["n_updates"] = int(rng.integers(25, 60))
        else:
            params["n_updates"] = int(rng.integers(25, 50))
            params["n_creates"] += int(rng.integers(10, 25))

    elif scenario == "subtle_snooping":
        params["n_views"] = int(max(p["views"], 1) * rng.uniform(1.5, 2.2))
        params["n_searches"] = int(params["n_searches"] * 1.3)

    else:
        raise ValueError(f"Unknown scenario: {scenario}")
    return params


def build_day(rng, user_id, role, day, params):
    """Create the raw event rows for one user on one local day."""
    day_start = datetime.combine(day, time(0, 0, 0))
    start = day_start + timedelta(hours=max(params["login_hour"], 0.25))
    end = min(start + timedelta(hours=params["session_hours"]),
              day_start + timedelta(hours=params["cap_hour"]))
    span = max((end - start).total_seconds(), 60.0)
    events = []

    def add(action, when, resource_type=None, resource_id=None, records=0, success=1):
        utc = when - timedelta(minutes=settings.TIMEZONE_OFFSET_MINUTES)
        events.append({
            "user_id": user_id, "role": role, "action": action,
            "resource_type": resource_type, "resource_id": resource_id,
            "records_accessed": records, "success": success,
            "timestamp": utc.strftime(TS_FORMAT),
        })

    def times(n):
        return sorted(start + timedelta(seconds=float(s)) for s in rng.uniform(0, span, size=n))

    def patient_id():
        return int(rng.integers(1, PATIENT_POOL + 1))

    back_limit = (start - day_start).total_seconds() - 60.0
    window = max(min(params["fail_window_s"], back_limit), 2.0)
    for _ in range(params["n_failed"]):
        add("login_failed", start - timedelta(seconds=float(rng.uniform(1.0, window))),
            success=0)

    if not params["logged_in"]:
        return events

    add("login", start)
    for t in times(params["n_views"]):
        add("view_patient", t, "patient", patient_id(), 1)
    for t in times(params["n_searches"]):
        add("search_patients", t, "patient", None, int(rng.integers(1, 6)))
    for t in times(params["n_lists"]):
        add("list_patients", t, "patient", None, int(rng.integers(8, 21)))
    for t in times(params["n_updates"]):
        if role == "receptionist":
            action = "update_patient"
        else:
            action = str(rng.choice(["update_patient", "update_medical_record"]))
        rtype = "patient" if action == "update_patient" else "medical_record"
        add(action, t, rtype, patient_id(), 1)
    for t in times(params["n_creates"]):
        if role == "receptionist":
            add("register_patient", t, "patient", patient_id(), 0)
        else:
            add("create_medical_record", t, "medical_record", patient_id(), 0)
    for t in times(params["n_denied"]):
        add("access_denied", t, "route", None, 0, success=0)
    for t in times(params["n_audits"]):
        add("view_audit_logs", t, "activity_log", None, 0)
    add("logout", end)
    return events


def generate(seed=settings.RANDOM_SEED, n_days=180, train_days=150,
             train_rate=0.03, test_rate=0.08, start_date=date(2025, 1, 6)):
    """Returns (events_df, labels_df, meta). Same seed -> identical output.

    train_rate / test_rate: chance that a user-day is replaced by an anomalous
    scenario. The test rate is inflated so the test split has enough positives;
    precision therefore depends on this prevalence (see docs/ML_DESIGN.md)."""
    if not 0 < train_days < n_days:
        raise ValueError("train_days must be between 1 and n_days - 1")
    rng = np.random.default_rng(seed)

    users, next_id = [], 1001
    for role in settings.ROLES:
        for _ in range(PROFILES[role]["users"]):
            users.append((next_id, role))
            next_id += 1

    events, labels = [], []
    for d in range(n_days):
        day = start_date + timedelta(days=d)
        weekend = day.weekday() >= 5
        split = "train" if d < train_days else "test"
        rate = train_rate if split == "train" else test_rate
        for user_id, role in users:
            prof = PROFILES[role]
            scenario = "normal"
            if rng.random() < rate:
                scenario = str(rng.choice(SCENARIOS[role]))
            elif rng.random() >= (prof["weekend_p"] if weekend else prof["weekday_p"]):
                continue  # day off
            params = normal_params(rng, role)
            if scenario != "normal":
                apply_scenario(rng, role, scenario, params)
            events.extend(build_day(rng, user_id, role, day, params))
            labels.append({"user_id": user_id, "role": role, "date": day.isoformat(),
                           "split": split, "is_anomaly": int(scenario != "normal"),
                           "scenario": scenario})

    events_df = pd.DataFrame(events).sort_values("timestamp", kind="stable").reset_index(drop=True)
    labels_df = pd.DataFrame(labels)
    meta = {
        "seed": seed, "n_days": n_days, "train_days": train_days,
        "start_date": start_date.isoformat(),
        "split_date": (start_date + timedelta(days=train_days)).isoformat(),
        "train_anomaly_rate_per_user_day": train_rate,
        "test_anomaly_rate_per_user_day": test_rate,
        "timezone_offset_minutes": settings.TIMEZONE_OFFSET_MINUTES,
        "n_events": int(len(events_df)),
        "n_user_days": int(len(labels_df)),
        "n_anomalous_user_days": int(labels_df["is_anomaly"].sum()),
        "users_per_role": {r: int(PROFILES[r]["users"]) for r in settings.ROLES},
    }
    return events_df, labels_df, meta


def save_dataset(events, labels, meta):
    os.makedirs(settings.DATA_DIR, exist_ok=True)
    events.to_csv(settings.EVENTS_PATH, index=False)
    labels.to_csv(settings.LABELS_PATH, index=False)
    with open(settings.META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic activity logs.")
    parser.add_argument("--seed", type=int, default=settings.RANDOM_SEED)
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--train-days", type=int, default=150)
    args = parser.parse_args()

    events, labels, meta = generate(seed=args.seed, n_days=args.days,
                                    train_days=args.train_days)
    save_dataset(events, labels, meta)

    print(f"Wrote {meta['n_events']} events for {meta['n_user_days']} user-days.")
    print(f"Split date (first test day): {meta['split_date']}")
    print("User-days by split and label:")
    print(labels.groupby(["split", "is_anomaly"]).size().to_string())
    print("Anomalous user-days by scenario:")
    print(labels[labels["is_anomaly"] == 1].groupby("scenario").size().to_string())
    print(f"Files written to {settings.DATA_DIR}")


if __name__ == "__main__":
    main()