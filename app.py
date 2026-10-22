import os

from flask import Flask, render_template

import database
import security
from config import Config
from routes.admin import bp as admin_bp
from routes.auth import bp as auth_bp
from routes.main import bp as main_bp
from routes.patients import bp as patients_bp

ERROR_TEXT = {
    400: ("Bad request", None),  # None -> show the specific description
    403: ("Access denied", "Your role does not have permission to access this page."),
    404: ("Page not found", "The page you requested does not exist."),
    405: ("Method not allowed", "This URL does not accept that kind of request."),
}


def register_error_handlers(app):
    def handler(err):
        code = err.code or 500
        title, text = ERROR_TEXT.get(code, ("Error", "Something went wrong."))
        if text is None:
            text = err.description
        return render_template("error.html", code=code, title=title, text=text), code

    for code in ERROR_TEXT:
        app.register_error_handler(code, handler)


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    # Make sure the folder that holds the SQLite file exists.
    os.makedirs(os.path.dirname(app.config["DATABASE"]), exist_ok=True)

    database.init_app(app)
    security.init_app(app)

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(patients_bp)

    register_error_handlers(app)
    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)