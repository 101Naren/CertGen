"""Retrieving generated certificates."""
import io
import zipfile

from app import worker
from tests.conftest import create_job


def first_certificate_id(client, job_id, status="generated"):
    rows = client.get(f"/jobs/{job_id}/certificates", params={"status": status}).json()
    return rows[0]["id"]


# ---------- single PDF ----------

def test_download_single_certificate_as_pdf(client):
    job_id = create_job(client)
    worker.process_job(job_id)

    response = client.get(f"/certificates/{first_certificate_id(client, job_id)}/download")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert "certificate_Asha_Rao.pdf" in response.headers["content-disposition"]


def test_download_unknown_certificate_is_404(client):
    assert client.get("/certificates/nope/download").status_code == 404


def test_download_of_certificate_not_generated_yet_is_409(client):
    job_id = create_job(client)                                # still pending
    cert_id = first_certificate_id(client, job_id, status="pending")

    assert client.get(f"/certificates/{cert_id}/download").status_code == 409


def test_download_of_invalid_row_is_409(client):
    job_id = create_job(client, recipients=[{"name": "", "email": "x"},
                                            {"name": "Ok", "email": "ok@example.com"}])
    cert_id = first_certificate_id(client, job_id, status="invalid")

    assert client.get(f"/certificates/{cert_id}/download").status_code == 409


# ---------- ZIP of a whole job ----------

def test_download_job_zip_contains_only_generated_certificates(client):
    job_id = create_job(client, recipients=[
        {"name": "Asha Rao", "email": "asha@example.com"},
        {"name": "Bad Row", "email": "nope"},                  # invalid: no PDF
        {"name": "Vikram Iyer", "email": "vikram@example.com"},
    ])
    worker.process_job(job_id)

    response = client.get(f"/jobs/{job_id}/download")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert archive.testzip() is None
    assert sorted(archive.namelist()) == ["0001_Asha_Rao.pdf", "0003_Vikram_Iyer.pdf"]
    assert archive.read("0001_Asha_Rao.pdf").startswith(b"%PDF")


def test_job_zip_is_409_while_job_is_still_running(client):
    job_id = create_job(client)                                # not processed yet

    assert client.get(f"/jobs/{job_id}/download").status_code == 409


def test_job_zip_is_409_when_nothing_was_generated(client):
    job_id = create_job(client, recipients=[{"name": "", "email": "x"}])

    assert client.get(f"/jobs/{job_id}/download").status_code == 409


def test_job_zip_for_unknown_job_is_404(client):
    assert client.get("/jobs/nope/download").status_code == 404


def test_hostile_name_cannot_escape_zip_or_storage_folder(client):
    job_id = create_job(client, recipients=[{"name": "../../etc/passwd", "email": "x@example.com"}])
    worker.process_job(job_id)

    archive = zipfile.ZipFile(io.BytesIO(client.get(f"/jobs/{job_id}/download").content))

    (entry,) = archive.namelist()
    assert ".." not in entry and "/" not in entry
    assert entry.endswith(".pdf")


# ---------- listing ----------

def test_list_certificates_can_be_filtered_by_status(client):
    job_id = create_job(client, recipients=[
        {"name": "Good", "email": "good@example.com"},
        {"name": "Bad", "email": "nope"},
    ])
    worker.process_job(job_id)

    invalid = client.get(f"/jobs/{job_id}/certificates", params={"status": "invalid"}).json()
    generated = client.get(f"/jobs/{job_id}/certificates", params={"status": "generated"}).json()

    assert [r["row_index"] for r in invalid] == [1]
    assert [r["row_index"] for r in generated] == [0]


def test_list_with_unknown_status_value_is_422(client):
    job_id = create_job(client)

    assert client.get(f"/jobs/{job_id}/certificates", params={"status": "bogus"}).status_code == 422


# ---------- optional one-page form ----------

def test_home_page_serves_the_form(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Generate certificates" in response.text
