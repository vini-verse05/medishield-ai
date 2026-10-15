import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    # Set SECRET_KEY as an environment variable for anything beyond local demos.
    # The fallback is for local development only.
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
    DATABASE = os.path.join(BASE_DIR, "instance", "medishield.db")