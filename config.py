import os
from datetime import timedelta

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    # Set SECRET_KEY as an environment variable for anything beyond local demos.
    # The fallback is for local development only.
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
    DATABASE = os.path.join(BASE_DIR, "instance", "medishield.db")

    # Session / cookie hardening
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Set SESSION_COOKIE_SECURE=1 when serving over HTTPS.
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"
    # Permanent sessions are refreshed on each request, so this acts as an idle timeout.
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)