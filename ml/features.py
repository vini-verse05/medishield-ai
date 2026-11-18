"""Feature extraction: turns raw activity-log rows into one row of numbers per
user per local day.

The same function prepares the synthetic training data and the real logs from
the running app, so the model always sees the same kind of input."""
import pandas as pd

from ml import settings

# Columns the input table must have (they match the activity_logs table).
REQUIRED_COLUMNS = ["user_id", "role", "action", "resource_id",
                    "records_accessed", "success", "timestamp"]
# Which log actions count as browsing (list/search) and as changing data.
LIST_SEARCH_ACTIONS = {"list_patients", "search_patients"}
WRITE_ACTIONS = {"update_patient", "update_medical_record",
                 "create_medical_record", "register_patient"}


def empty_features():
    """An empty feature table with the right columns."""
    return pd.DataFrame(columns=settings.KEY_COLUMNS + settings.FEATURE_COLUMNS)


def extract_features(events, tz_offset_minutes=None):
    """events: DataFrame with the columns in REQUIRED_COLUMNS (timestamps are
    naive UTC strings, as stored by the app). Returns one row per
    (user_id, role, local date) with settings.FEATURE_COLUMNS."""
    if tz_offset_minutes is None:
        tz_offset_minutes = settings.TIMEZONE_OFFSET_MINUTES

    missing = [c for c in REQUIRED_COLUMNS if c not in events.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    df = events[REQUIRED_COLUMNS].copy()
    # Failed logins for unknown usernames have no user/role: they cannot be
    # attributed to a role baseline, so they are excluded here.
    df = df.dropna(subset=["user_id", "role"])
    df = df[df["role"].isin(settings.ROLES)].reset_index(drop=True)
    if df.empty:
        return empty_features()

    df["user_id"] = df["user_id"].astype(int)
    local = pd.to_datetime(df["timestamp"]) + pd.Timedelta(minutes=tz_offset_minutes)
    df["date"] = local.dt.strftime("%Y-%m-%d")
    df["hour"] = local.dt.hour

    action = df["action"]
    df["records_accessed"] = pd.to_numeric(df["records_accessed"], errors="coerce").fillna(0)
    df["success"] = pd.to_numeric(df["success"], errors="coerce").fillna(0)

    df["is_failed_login"] = (action == "login_failed").astype(int)
    df["is_denied"] = (action == "access_denied").astype(int)
    df["is_list_search"] = action.isin(LIST_SEARCH_ACTIONS).astype(int)
    df["is_write"] = action.isin(WRITE_ACTIONS).astype(int)
    df["is_off_hours"] = ((df["hour"] >= settings.OFF_HOURS_START)
                          | (df["hour"] < settings.OFF_HOURS_END)).astype(int)
    df["viewed_patient_id"] = pd.to_numeric(df["resource_id"], errors="coerce").where(
        action == "view_patient")
    df["login_hour_value"] = df["hour"].where((action == "login") & (df["success"] == 1))

    keys = settings.KEY_COLUMNS
    out = df.groupby(keys).agg(
        total_events=("action", "size"),
        first_hour=("hour", "min"),
        login_hour=("login_hour_value", "min"),
        off_hours_actions=("is_off_hours", "sum"),
        failed_logins=("is_failed_login", "sum"),
        records_accessed=("records_accessed", "sum"),
        patients_viewed=("viewed_patient_id", "nunique"),
        list_search_actions=("is_list_search", "sum"),
        write_actions=("is_write", "sum"),
        denied_actions=("is_denied", "sum"),
    )
    peak = df.groupby(keys + ["hour"]).size().groupby(level=keys).max()
    out["peak_hourly_events"] = peak
    # Days without a successful login use the hour of their first event.
    out["login_hour"] = out["login_hour"].fillna(out["first_hour"])

    out = out.reset_index()
    out = out[keys + settings.FEATURE_COLUMNS]
    out[settings.FEATURE_COLUMNS] = out[settings.FEATURE_COLUMNS].astype(float)
    return out.sort_values(["date", "user_id"]).reset_index(drop=True)