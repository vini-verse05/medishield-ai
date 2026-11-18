"""Database access: opens the SQLite connection for each request and provides
the commands that create, upgrade and seed the database.

Commands (run from the project folder):
    flask --app app init-db [--reset]   create the tables (--reset deletes the file first)
    flask --app app migrate-db          upgrade an older database to the current schema
    flask --app app seed-db             insert the fictional sample data
"""
import os
import sqlite3

import click
from flask import current_app, g
from flask.cli import with_appcontext

# Columns added to the `alerts` table after its first version (name, SQL type).
# migrate_db() adds them to databases created before they existed.
ALERT_NEW_COLUMNS = [
    ("alert_date", "TEXT"),
    ("threshold", "REAL"),
    ("signals_json", "TEXT"),
]


def get_db():
    """Return a per-request SQLite connection with foreign keys enforced."""
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    """Close the request's connection when the request ends."""
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


def migrate_db():
    """Upgrade an older database to the current schema.
    Safe to run more than once, and on a new or already-upgraded database."""
    db = get_db()
    existing = {r["name"] for r in db.execute("PRAGMA table_info(alerts)")}
    if existing:  # an older alerts table exists: add the columns it lacks
        for name, sql_type in ALERT_NEW_COLUMNS:
            if name not in existing:
                db.execute(f"ALTER TABLE alerts ADD COLUMN {name} {sql_type}")
        db.commit()
    # schema.sql only uses IF NOT EXISTS, so this adds what is missing
    # (analysis_results, new indexes) and leaves existing tables and data alone.
    with current_app.open_resource("schema.sql") as f:
        db.executescript(f.read().decode("utf-8"))
    db.commit()


@click.command("init-db")
@click.option("--reset", is_flag=True, help="Delete the existing database first.")
@with_appcontext
def init_db_command(reset):
    """Initialize the database tables."""
    init_db(reset=reset)
    click.echo("Database reset and initialized." if reset else "Database initialized.")


@click.command("migrate-db")
@with_appcontext
def migrate_db_command():
    """Upgrade an existing database to the current schema (safe to repeat)."""
    migrate_db()
    click.echo("Database migrated to the current schema.")


@click.command("seed-db")
@with_appcontext
def seed_db_command():
    """Insert clearly labelled sample data (skips if users already exist)."""
    from seed_data import seed_database

    summary = seed_database(get_db())
    click.echo(summary)


def init_app(app):
    """Hook the database into the Flask app: close connections and add the commands."""
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(migrate_db_command)
    app.cli.add_command(seed_db_command)