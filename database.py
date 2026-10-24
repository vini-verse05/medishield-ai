import os
import sqlite3

import click
from flask import current_app, g
from flask.cli import with_appcontext

# Columns added to `alerts` in Part 5 (name, SQL type).
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
    """Upgrade an existing Part 1-4 database to the current schema.
    Idempotent: safe to run on a new or already-migrated database."""
    db = get_db()
    existing = {r["name"] for r in db.execute("PRAGMA table_info(alerts)")}
    if existing:  # an older alerts table is present: add the new columns
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
    """Upgrade an existing database to the Part 5 schema (safe to repeat)."""
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
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(migrate_db_command)
    app.cli.add_command(seed_db_command)