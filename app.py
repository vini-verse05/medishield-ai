"""MediShield AI application entry point.

Builds the Flask app, connects the database and security layers, registers the
page blueprints (login, patients, alerts, admin) and the error pages.

Run with:  flask --app app run --debug
"""
import os
import sqlite3
from datetime import datetime, timedelta

from flask import Flask, g, render_template

import analysis
import database
import security
from config import Config
from ml import settings
from routes.admin import bp as admin_bp
from routes.alerts import bp as alerts_bp
from routes.auth import bp as auth_bp
from routes.main import bp as main_bp
from routes.patients import bp as patients_bp

# Title and message for each error page. None means "use the error's own text".
ERROR_TEXT = {
    400: ("Bad request", None),
    403: ("Access denied", "Your role does not have permission to access this page."),
    404: ("Page not found", "The page you requested does not exist."),
    405: ("Method not allowed", "This URL does not accept that kind of request."),
}


def register_error_handlers(app):
    """Show a friendly error page for 400, 403, 404 and 405 responses."""
    def handler(err):
        code = err.code or 500
        title, text = ERROR_TEXT.get(code, ("Error", "Something went wrong."))
        if text is None:
            text = err.description
        return render_template("error.html", code=code, title=title, text=text), code

    for code in ERROR_TEXT:
        app.register_error_handler(code, handler)


def register_nav_counts(app):
    """Give every template the number of new alerts (administrators only)."""
    @app.context_processor
    def nav_counts():
        user = g.get("user")
        count = 0
        if user is not None and security.has_permission(user["role"], "view_alerts"):
            try:
                count = database.get_db().execute(
                    "SELECT COUNT(*) FROM alerts WHERE status = 'new'").fetchone()[0]
            except sqlite3.Error:
                count = 0  # alerts table missing: run `flask --app app migrate-db`
        return {"new_alert_count": count}


def register_filters(app):
    """Add the `localtime` template filter."""
    @app.template_filter("localtime")
    def localtime(value):
        """Show a stored UTC timestamp in local time (offset set in ml/settings.py)."""
        if not value:
            return value
        try:
            dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return value
        local = dt + timedelta(minutes=settings.TIMEZONE_OFFSET_MINUTES)
        return local.strftime("%Y-%m-%d %H:%M:%S")


def create_app(test_config=None):
    """Create and configure the app. Tests pass `test_config` to override settings."""
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    # The folder that holds the SQLite file must exist before the first connection.
    os.makedirs(os.path.dirname(app.config["DATABASE"]), exist_ok=True)

    database.init_app(app)
    analysis.init_app(app)
    security.init_app(app)
    register_nav_counts(app)
    register_filters(app)

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(patients_bp)
    app.register_blueprint(alerts_bp)

    register_error_handlers(app)
    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
