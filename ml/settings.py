"""Shared constants for the ML pipeline. After changing any value here,
regenerate the data and retrain."""
import os

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ML_DIR = os.path.join(BASE_DIR, "ml")
DATA_DIR = os.path.join(ML_DIR, "data")
MODEL_DIR = os.path.join(ML_DIR, "models")
REPORT_DIR = os.path.join(ML_DIR, "reports")

EVENTS_PATH = os.path.join(DATA_DIR, "synthetic_activity_logs.csv")
LABELS_PATH = os.path.join(DATA_DIR, "synthetic_labels.csv")
META_PATH = os.path.join(DATA_DIR, "synthetic_meta.json")
MODEL_PATH = os.path.join(MODEL_DIR, "isolation_forest_by_role.joblib")

# The app stores UTC. Features use local time (330 = IST, UTC+05:30).
TIMEZONE_OFFSET_MINUTES = 330

# Local hours treated as "off hours": 22:00-05:59.
OFF_HOURS_START = 22
OFF_HOURS_END = 6

ROLES = ["admin", "doctor", "nurse", "receptionist"]
KEY_COLUMNS = ["user_id", "role", "date"]
FEATURE_COLUMNS = [
    "login_hour",
    "off_hours_actions",
    "failed_logins",
    "records_accessed",
    "patients_viewed",
    "list_search_actions",
    "write_actions",
    "denied_actions",
    "total_events",
    "peak_hourly_events",
]

RANDOM_SEED = 42
N_ESTIMATORS = 200
# Share of each role's training days that will be flagged (sets the threshold).
TARGET_FLAG_RATE = 0.05
MIN_TRAIN_ROWS = 30