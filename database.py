import os
import sqlite3

import click
from flask import current_app, g


def get_db():
    """Return a per-request SQLite connection with foreign keys enforced."""
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(reset=False):
    """Create tables from schema.sql. With reset=True the DB file is deleted first."""
    path = current_app.config["DATABASE"]
    if reset and os.path.exists(path):
        os.remove(path)
    db = get_db()
    with current_app.open_resource("schema.sql") as f:
        db.executescript(f.read().decode("utf-8"))
    db.commit()


@click.command("init-db")
@click.option("--reset", is_flag=True, help="Delete the existing database first.")
def init_db_command(reset):
    """Initialize the database tables."""
    init_db(reset=reset)
    click.echo("Database reset and initialized." if reset else "Database initialized.")


@click.command("seed-db")
def seed_db_command():
    """Insert clearly labelled sample data (skips if users already exist)."""
    from seed_data import seed_database

    summary = seed_database(get_db())
    click.echo(summary)


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(seed_db_command)