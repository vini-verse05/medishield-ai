"""One-command setup: creates/upgrades the database, inserts the fictional
sample data, and prepares the ML model (generates synthetic data and trains).
Safe to run more than once.

Usage:
    python setup_project.py              # normal setup
    python setup_project.py --reset-db   # delete the database first
    python setup_project.py --retrain    # regenerate data and retrain the model
"""
import argparse
import os


def main():
    parser = argparse.ArgumentParser(description="Set up MediShield AI.")
    parser.add_argument("--reset-db", action="store_true")
    parser.add_argument("--retrain", action="store_true")
    args = parser.parse_args()

    from app import app
    import database
    from ml import settings
    from seed_data import seed_database

    print("1/3 Database")
    path = app.config["DATABASE"]
    if args.reset_db and os.path.exists(path):
        os.remove(path)
        print("  Existing database deleted.")
    with app.app_context():
        database.migrate_db()  # creates missing tables; upgrades older databases
        print("  " + seed_database(database.get_db()))

    print("2/3 ML model")
    if args.retrain or not os.path.exists(settings.MODEL_PATH):
        from ml.generate_data import generate, save_dataset
        from ml.train import train_from_files

        events, labels, meta = generate()
        save_dataset(events, labels, meta)
        train_from_files()
        print(f"  Synthetic data generated and model saved to {settings.MODEL_PATH}")
    else:
        print("  Model already exists (use --retrain to rebuild it).")

    print("3/3 Done")
    print("  Run the tests : python run_all_tests.py")
    print("  Start the app : flask --app app run --debug")
    print("  Open          : http://127.0.0.1:5000/login")


if __name__ == "__main__":
    main()