-- MediShield AI database schema (Parts 1 and 5)
-- Timestamps are stored as UTC text: 'YYYY-MM-DD HH:MM:SS'

CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    username            TEXT NOT NULL UNIQUE,
    password_hash       TEXT NOT NULL,
    full_name           TEXT NOT NULL,
    role                TEXT NOT NULL
                        CHECK (role IN ('admin', 'doctor', 'nurse', 'receptionist')),
    department          TEXT,
    is_active           INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    failed_login_count  INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS patients (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mrn             TEXT NOT NULL UNIQUE,          -- medical record number (sample IDs)
    first_name      TEXT NOT NULL,
    last_name       TEXT NOT NULL,
    date_of_birth   TEXT NOT NULL,                 -- YYYY-MM-DD
    gender          TEXT CHECK (gender IN ('Male', 'Female', 'Other')),
    phone           TEXT,
    address         TEXT,
    blood_group     TEXT,
    allergies       TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS medical_records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id  INTEGER NOT NULL,
    record_type TEXT NOT NULL,                     -- e.g. Consultation, Lab Report
    diagnosis   TEXT,
    treatment   TEXT,
    notes       TEXT,
    created_by  INTEGER NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_by  INTEGER,
    updated_at  TEXT,
    FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by) REFERENCES users(id),
    FOREIGN KEY (updated_by) REFERENCES users(id)
);

-- Audit / activity log. Written only by server-side code.
CREATE TABLE IF NOT EXISTS activity_logs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER,                   -- NULL for failed logins of unknown users
    username_attempted  TEXT,                      -- useful for failed logins
    role                TEXT,
    action              TEXT NOT NULL,             -- login, logout, login_failed, view_patient, ...
    resource_type       TEXT,                      -- patient, medical_record, ...
    resource_id         INTEGER,
    records_accessed    INTEGER NOT NULL DEFAULT 0,
    success             INTEGER NOT NULL DEFAULT 1 CHECK (success IN (0, 1)),
    ip_address          TEXT,
    user_agent          TEXT,
    details             TEXT,
    timestamp           TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

-- One alert per flagged user-day (see idx_alerts_user_date below).
CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_log_id INTEGER,                       -- latest log row of that user-day at analysis time
    user_id         INTEGER,
    anomaly_score   REAL,                          -- higher = more unusual for the user's role
    risk_level      TEXT NOT NULL
                    CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    explanation     TEXT,
    status          TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'reviewed')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    reviewed_by     INTEGER,
    reviewed_at     TEXT,
    alert_date      TEXT,                          -- local date of the analysed user-day (Part 5)
    threshold       REAL,                          -- the role's threshold when scored (Part 5)
    signals_json    TEXT,                          -- contributing signals as JSON (Part 5)
    FOREIGN KEY (activity_log_id) REFERENCES activity_logs(id) ON DELETE SET NULL,
    FOREIGN KEY (user_id)         REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (reviewed_by)     REFERENCES users(id) ON DELETE SET NULL
);

-- Model output for every analysed user-day, flagged or not (Part 5).
CREATE TABLE IF NOT EXISTS analysis_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    role            TEXT NOT NULL,
    result_date     TEXT NOT NULL,                 -- local date, YYYY-MM-DD
    anomaly_score   REAL NOT NULL,
    threshold       REAL NOT NULL,
    is_anomaly      INTEGER NOT NULL CHECK (is_anomaly IN (0, 1)),
    features_json   TEXT NOT NULL,
    scored_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, result_date),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_records_patient   ON medical_records(patient_id);
CREATE INDEX IF NOT EXISTS idx_logs_user         ON activity_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_logs_timestamp    ON activity_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_logs_action       ON activity_logs(action);
CREATE INDEX IF NOT EXISTS idx_alerts_status     ON alerts(status);
CREATE INDEX IF NOT EXISTS idx_results_date      ON analysis_results(result_date);
-- Duplicate guard: at most one alert per user per day (rows with NULL date are exempt).
CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_user_date ON alerts(user_id, alert_date);