import os

from flask import Flask

import database
from config import Config
from routes.main import bp as main_bp


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    # Make sure the folder that holds the SQLite file exists.
    os.makedirs(os.path.dirname(app.config["DATABASE"]), exist_ok=True)

    database.init_app(app)
    app.register_blueprint(main_bp)
    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)