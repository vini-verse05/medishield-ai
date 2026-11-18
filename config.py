"""Application settings. Values that differ between machines come from
environment variables, so no secrets are stored in the code."""
import os
from datetime import timedelta

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    # Signs the login session. Set the SECRET_KEY environment variable for
    # anything beyond a local demo; the fallback below is for development only.
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")

    # The SQLite database file.
    DATABASE = os.path.join(BASE_DIR, "instance", "medishield.db")

    # ---- Session cookie hardening ----
    SESSION_COOKIE_HTTPONLY = True   # not readable from JavaScript
    SESSION_COOKIE_SAMESITE = "Lax"  # not sent with most cross-site requests
    # Set SESSION_COOKIE_SECURE=1 when the site is served over HTTPS.
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"
    # The session is refreshed on every request, so this works as an idle timeout.
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)

    # Show the demo accounts on the login page. Set SHOW_DEMO_CREDENTIALS=0 to hide them.
    SHOW_DEMO_CREDENTIALS = os.environ.get("SHOW_DEMO_CREDENTIALS", "1") == "1"
