"""Creates demo activity by driving the REAL app routes (the same code paths
a browser would use) against your REAL database, so the audit log fills with
genuine rows. Nothing here fakes a model result.

Usage:
    python demo_activity.py bulk     # nurse_priya reads patient data in bulk
    python demo_activity.py attack   # 20 wrong-password attempts on recep_anita
Then:
    flask --app app analyze
"""
import sys

from app import app
from seed_data import DEMO_PASSWORD


def post(client, url, data):
    with client.session_transaction() as s:
        s["_csrf_token"] = "demo-token"
    return client.post(url, data={**data, "csrf_token": "demo-token"})


def bulk():
    client = app.test_client()
    r = post(client, "/login", {"username": "nurse_priya", "password": DEMO_PASSWORD})
    if r.status_code != 302:
        sys.exit("Login failed. Is the database initialised and seeded?")
    for _ in range(150):
        client.get("/patients")
    for _ in range(60):
        client.get("/patients?q=a")
    for i in range(90):
        client.get(f"/patients/{(i % 8) + 1}")
    print("Done: nurse_priya made ~300 patient list/search/view requests.")


def attack():
    client = app.test_client()
    for _ in range(20):
        post(client, "/login", {"username": "recep_anita", "password": "wrong-password"})
    print("Done: 20 failed logins recorded for recep_anita.")


if __name__ == "__main__":
    modes = {"bulk": bulk, "attack": attack}
    if len(sys.argv) != 2 or sys.argv[1] not in modes:
        sys.exit("Usage: python demo_activity.py bulk|attack")
    modes[sys.argv[1]]()
    print("Next: flask --app app analyze")