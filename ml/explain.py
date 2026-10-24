"""Risk levels and human-readable explanations for flagged user-days.
Pure Python (no Flask, no database) so it can be tested on its own.
The rules are documented in docs/RISK_AND_ALERTS.md."""
from ml import settings

LEVELS = ["low", "medium", "high", "critical"]

# Score position: 0 = at the role's threshold, 1 = the most unusual day seen
# in that role's training data.
BAND_MEDIUM = 0.25
BAND_HIGH = 0.60
BAND_CRITICAL = 1.00

# Rules that raise the level by one step (never above "critical").
ESCALATE_FAILED_LOGINS = 10
ESCALATE_DENIED_ACTIONS = 10

MAX_SIGNALS = 4
LOGIN_HOUR_SIGNAL_RATIO = 1.5  # sort weight of an off-hours login signal

FEATURE_LABELS = {
    "login_hour": "first login hour",
    "off_hours_actions": "actions between 22:00 and 06:00",
    "failed_logins": "failed logins",
    "records_accessed": "records accessed",
    "patients_viewed": "distinct patients viewed",
    "list_search_actions": "patient list/search requests",
    "write_actions": "record changes (updates, creations, registrations)",
    "denied_actions": "denied (unauthorised) requests",
    "total_events": "total actions",
    "peak_hourly_events": "actions in the busiest hour",
}


def fmt_num(x):
    x = float(x)
    return str(int(round(x))) if abs(x - round(x)) < 1e-9 else f"{x:.1f}"


def score_ratio(score, threshold, reference_max):
    """0 at the threshold, 1 at the highest training score for the role."""
    span = reference_max - threshold
    if span <= 1e-9:
        return 1.0 if score > threshold else 0.0
    return (score - threshold) / span


def score_band(ratio):
    if ratio >= BAND_CRITICAL:
        return "critical"
    if ratio >= BAND_HIGH:
        return "high"
    if ratio >= BAND_MEDIUM:
        return "medium"
    return "low"


def escalation_reasons(features, baseline):
    reasons = []
    if features["failed_logins"] >= ESCALATE_FAILED_LOGINS:
        reasons.append(f"{fmt_num(features['failed_logins'])} failed logins in one day")
    if features["denied_actions"] >= ESCALATE_DENIED_ACTIONS:
        reasons.append(f"{fmt_num(features['denied_actions'])} denied requests in one day")
    if (features["off_hours_actions"] > 0
            and features["records_accessed"] > baseline["records_accessed"]["p95"]):
        reasons.append("above-normal record access combined with off-hours activity")
    return reasons


def determine_risk(score, threshold, reference_max, features, baseline):
    ratio = score_ratio(score, threshold, reference_max)
    base = score_band(ratio)
    reasons = escalation_reasons(features, baseline)
    level, escalated = base, False
    if reasons and base != LEVELS[-1]:
        level = LEVELS[LEVELS.index(base) + 1]
        escalated = True
    return {
        "risk_level": level,
        "base_level": base,
        "score_ratio": float(ratio),
        "escalated": escalated,
        "escalation_reasons": reasons,
    }


def build_signals(features, baseline, limit=MAX_SIGNALS):
    """Measures that are unusual for the role, strongest first."""
    signals = []
    for name in settings.FEATURE_COLUMNS:
        value = float(features[name])
        b = baseline[name]
        label = FEATURE_LABELS[name]
        if name == "login_hour":
            if value < settings.OFF_HOURS_END or value >= settings.OFF_HOURS_START:
                signals.append({
                    "feature": name, "label": label, "value": value,
                    "median": b["median"], "p95": b["p95"],
                    "ratio": LOGIN_HOUR_SIGNAL_RATIO,
                    "text": f"first login at {int(value):02d}:00, outside normal working hours",
                })
            continue
        if value > b["p95"] and value > b["median"]:
            signals.append({
                "feature": name, "label": label, "value": value,
                "median": b["median"], "p95": b["p95"],
                "ratio": value / max(b["p95"], 1.0),
                "text": (f"{label}: {fmt_num(value)} (typical {fmt_num(b['median'])}, "
                         f"95th percentile {fmt_num(b['p95'])})"),
            })
    signals.sort(key=lambda s: s["ratio"], reverse=True)
    return signals[:limit]


def build_explanation(role, signals, escalation_reasons_applied):
    if signals:
        text = (f"Unusual compared with typical {role} days. Contributing signals: "
                + "; ".join(s["text"] for s in signals) + ".")
    else:
        text = (f"The overall combination of activity differs from typical {role} days, "
                "although no single measure exceeded its usual range "
                "(this can include unusually low activity).")
    if escalation_reasons_applied:
        text += " Risk level raised one step: " + "; ".join(escalation_reasons_applied) + "."
    text += " This is a warning for investigation, not proof of malicious intent."
    return text