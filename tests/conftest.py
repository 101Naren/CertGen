"""Shared test setup.

Tests use their own throwaway SQLite file and storage folder, so they never touch
real data. The environment variables must be set BEFORE the app is imported,
because the settings and the database engine are created at import time.
"""
import os
import shutil
import tempfile

_TMP = tempfile.mkdtemp(prefix="certgen-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["STORAGE_DIR"] = f"{_TMP}/storage"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import database, worker  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state():
    """Empty database and empty storage folder before every test."""
    database.Base.metadata.drop_all(database.engine)
    database.init_db()
    shutil.rmtree(os.environ["STORAGE_DIR"], ignore_errors=True)
    yield


@pytest.fixture
def scheduled(monkeypatch):
    """Replace worker.submit so no thread starts; records the job ids instead.

    Tests then run the worker themselves (worker.process_job) at a moment they
    choose, which keeps them fast and free of timing problems.
    """
    job_ids: list[str] = []
    monkeypatch.setattr(worker, "submit", job_ids.append)
    return job_ids


@pytest.fixture
def client(scheduled):
    # Not used as a context manager, so the startup (lifespan) code is skipped.
    return TestClient(app)


@pytest.fixture
def live_client():
    """A client where the real background thread pool runs."""
    return TestClient(app)


VALID_REQUEST = {
    "title": "Full-Stack Workshop 2026",
    "issuer": "Example Tech Club",
    "issue_date": "2026-10-10",
    "recipients": [
        {"name": "Asha Rao", "email": "asha@example.com"},
        {"name": "Vikram Iyer", "email": "vikram@example.com"},
    ],
}


def make_request(recipients=None, **overrides):
    body = {**VALID_REQUEST, **overrides}
    if recipients is not None:
        body["recipients"] = recipients
    return body


def create_job(client, recipients=None, **overrides) -> str:
    """POST a job and return its id."""
    response = client.post("/jobs", json=make_request(recipients, **overrides))
    assert response.status_code == 202, response.text
    return response.json()["job_id"]
