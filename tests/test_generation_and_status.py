"""Certificate generation, job status/progress and individual failures."""
import datetime
import os
import time
from pathlib import Path

import pytest

from app import pdf, worker
from app.config import settings
from tests.conftest import create_job


def status_of(client, job_id):
    return client.get(f"/jobs/{job_id}").json()


# ---------- certificate generation ----------

def test_render_certificate_writes_a_valid_pdf(tmp_path):
    target = tmp_path / "out" / "cert.pdf"

    pdf.render_certificate(name="Asha Rao", title="Workshop", issuer="Club",
                           issue_date=datetime.date(2026, 10, 10),
                           certificate_id="abc", path=target)

    assert target.read_bytes().startswith(b"%PDF")
    assert not list(target.parent.glob("*.tmp"))               # no leftovers


def test_long_realistic_name_is_shrunk_to_fit_the_page(tmp_path):
    long_name = "Venkata Subramanian Ramanathan " * 3          # 93 characters
    pdf.render_certificate(name=long_name.strip(), title="Workshop", issuer="Club",
                           issue_date=datetime.date(2026, 10, 10),
                           certificate_id="abc", path=tmp_path / "c.pdf")
    assert (tmp_path / "c.pdf").exists()


def test_name_too_wide_even_at_the_smallest_size_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="too long"):
        pdf.render_certificate(name="W" * 100, title="Workshop", issuer="Club",
                               issue_date=datetime.date(2026, 10, 10),
                               certificate_id="abc", path=tmp_path / "c.pdf")
    assert not (tmp_path / "c.pdf").exists()                   # nothing half-written


def test_name_the_font_cannot_print_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="cannot print"):
        pdf.render_certificate(name="தமிழ்", title="Workshop", issuer="Club",
                               issue_date=datetime.date(2026, 10, 10),
                               certificate_id="abc", path=tmp_path / "c.pdf")


def test_worker_generates_one_pdf_per_valid_recipient(client):
    job_id = create_job(client)

    worker.process_job(job_id)

    rows = client.get(f"/jobs/{job_id}/certificates").json()
    assert [r["status"] for r in rows] == ["generated", "generated"]
    stored = list((Path(settings.storage_dir) / job_id).glob("*.pdf"))
    assert len(stored) == 2
    assert all(p.read_bytes().startswith(b"%PDF") for p in stored)


def test_processing_a_job_twice_does_not_regenerate_finished_certificates(client):
    job_id = create_job(client)
    worker.process_job(job_id)
    before = {r["id"]: os.path.getmtime(p)
              for r in client.get(f"/jobs/{job_id}/certificates").json()
              for p in (Path(settings.storage_dir) / job_id / f"{r['id']}.pdf",)}
    time.sleep(0.05)

    worker.process_job(job_id)

    after = {cid: os.path.getmtime(Path(settings.storage_dir) / job_id / f"{cid}.pdf")
             for cid in before}
    assert before == after


# ---------- job status / progress ----------

def test_status_before_processing_shows_everything_pending(client):
    job_id = create_job(client)

    status = status_of(client, job_id)

    assert status["status"] == "pending"
    assert (status["total"], status["pending"], status["generated"]) == (2, 2, 0)
    assert status["progress_percent"] == 0


def test_status_after_processing_is_completed_at_100_percent(client):
    job_id = create_job(client)
    worker.process_job(job_id)

    status = status_of(client, job_id)

    assert status["status"] == "completed"
    assert status["generated"] == 2
    assert status["progress_percent"] == 100
    assert status["errors"] == []


def test_counts_always_add_up_to_total(client):
    job_id = create_job(client, recipients=[
        {"name": "A", "email": "a@example.com"},
        {"name": "B", "email": "b"},                           # invalid
        {"name": "C", "email": "c@example.com"},
    ])
    worker.process_job(job_id)

    s = status_of(client, job_id)

    assert s["generated"] + s["failed"] + s["invalid"] + s["pending"] == s["total"] == 3
    assert s["status"] == "completed_with_errors"


def test_progress_counts_invalid_rows_as_done_before_processing(client):
    job_id = create_job(client, recipients=[
        {"name": "A", "email": "a@example.com"},
        {"name": "B", "email": "b"},
    ])

    assert status_of(client, job_id)["progress_percent"] == 50


def test_unknown_job_returns_404(client):
    assert client.get("/jobs/does-not-exist").status_code == 404


# ---------- one certificate failing ----------

@pytest.fixture
def explode_for_boom(monkeypatch):
    """Make the renderer crash for the recipient named 'Boom' only."""
    real = pdf.render_certificate

    def flaky(**kwargs):
        if kwargs["name"] == "Boom":
            raise RuntimeError("renderer exploded")
        return real(**kwargs)

    monkeypatch.setattr(pdf, "render_certificate", flaky)


def test_one_failing_certificate_does_not_stop_the_others(client, explode_for_boom):
    job_id = create_job(client, recipients=[
        {"name": "First", "email": "first@example.com"},
        {"name": "Boom", "email": "boom@example.com"},
        {"name": "Third", "email": "third@example.com"},
    ])

    worker.process_job(job_id)

    rows = {r["row_index"]: r for r in client.get(f"/jobs/{job_id}/certificates").json()}
    assert rows[0]["status"] == "generated"
    assert rows[1]["status"] == "failed"
    assert "renderer exploded" in rows[1]["error"]
    assert rows[2]["status"] == "generated"                    # still processed after the failure


def test_failed_row_is_reported_in_job_status(client, explode_for_boom):
    job_id = create_job(client, recipients=[
        {"name": "Fine", "email": "fine@example.com"},
        {"name": "Boom", "email": "boom@example.com"},
    ])
    worker.process_job(job_id)

    s = status_of(client, job_id)

    assert s["status"] == "completed_with_errors"
    assert (s["generated"], s["failed"]) == (1, 1)
    assert s["errors"][0]["row_index"] == 1
    assert s["errors"][0]["status"] == "failed"


def test_name_with_unprintable_characters_fails_only_that_row(client):
    job_id = create_job(client, recipients=[
        {"name": "தமிழ்", "email": "tamil@example.com"},
        {"name": "Asha Rao", "email": "asha@example.com"},
    ])

    worker.process_job(job_id)

    rows = client.get(f"/jobs/{job_id}/certificates").json()
    assert [r["status"] for r in rows] == ["failed", "generated"]


def test_job_fails_when_every_certificate_fails(client, monkeypatch):
    def always_fail(**kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(pdf, "render_certificate", always_fail)
    job_id = create_job(client)

    worker.process_job(job_id)

    s = status_of(client, job_id)
    assert s["status"] == "failed"
    assert s["failed"] == 2


# ---------- background processing and restart recovery ----------

def test_requeue_unfinished_resubmits_jobs_that_never_finished(client, scheduled):
    job_id = create_job(client)                                # created, never processed
    scheduled.clear()

    count = worker.requeue_unfinished()

    assert count == 1
    assert scheduled == [job_id]


def test_requeue_ignores_finished_jobs(client, scheduled):
    job_id = create_job(client)
    worker.process_job(job_id)
    scheduled.clear()

    assert worker.requeue_unfinished() == 0


def test_real_background_worker_finishes_a_job(live_client):
    """End to end with the real thread pool (the only test that waits on a thread)."""
    job_id = create_job(live_client)

    deadline = time.time() + 10
    while time.time() < deadline:
        status = status_of(live_client, job_id)
        if status["status"] not in ("pending", "processing"):
            break
        time.sleep(0.05)

    assert status["status"] == "completed"
    assert status["generated"] == 2
