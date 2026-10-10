"""Creating a generation job and input validation."""
import pytest

from tests.conftest import create_job, make_request


# ---------- creating a job ----------

def test_create_job_returns_202_and_schedules_work(client, scheduled):
    response = client.post("/jobs", json=make_request())

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"
    assert body["total"] == 2
    assert scheduled == [body["job_id"]]          # handed to the background worker


def test_new_job_has_one_pending_row_per_recipient(client):
    job_id = create_job(client)

    rows = client.get(f"/jobs/{job_id}/certificates").json()
    assert [r["row_index"] for r in rows] == [0, 1]
    assert {r["status"] for r in rows} == {"pending"}


# ---------- request-level validation: whole request rejected (422) ----------

@pytest.mark.parametrize("bad_body", [
    {},                                                        # nothing at all
    make_request(title=""),                                    # blank title
    make_request(issuer=""),                                   # blank issuer
    make_request(recipients=[]),                               # empty list
    make_request(issue_date="not-a-date"),                     # wrong type
    {**make_request(), "recipients": "asha@example.com"},      # not a list
])
def test_malformed_request_is_rejected_and_nothing_is_created(client, scheduled, bad_body):
    response = client.post("/jobs", json=bad_body)

    assert response.status_code == 422
    assert scheduled == []


def test_too_many_recipients_is_rejected(client):
    too_many = [{"name": f"P{i}", "email": f"p{i}@example.com"} for i in range(1001)]

    response = client.post("/jobs", json=make_request(recipients=too_many))

    assert response.status_code == 422


# ---------- row-level validation: bad rows recorded, good rows kept ----------

def test_bad_rows_are_marked_invalid_and_good_rows_stay_pending(client):
    job_id = create_job(client, recipients=[
        {"name": "Good One", "email": "good@example.com"},     # 0 ok
        {"name": "", "email": "blank@example.com"},            # 1 blank name
        {"name": "Bad Email", "email": "not-an-email"},        # 2 bad email
        "just a string",                                       # 3 not an object
        {"name": "No Email"},                                  # 4 missing email
        {"name": "Last Good", "email": "last@example.com"},    # 5 ok
    ])

    rows = {r["row_index"]: r for r in client.get(f"/jobs/{job_id}/certificates").json()}

    assert rows[0]["status"] == "pending"
    assert rows[5]["status"] == "pending"
    for index in (1, 2, 3, 4):
        assert rows[index]["status"] == "invalid"
        assert rows[index]["error"]                            # says what is wrong
    assert "name" in rows[1]["error"]
    assert "email" in rows[2]["error"]
    assert "email" in rows[4]["error"]


def test_invalid_rows_appear_in_job_errors_with_row_index(client):
    job_id = create_job(client, recipients=[
        {"name": "Good", "email": "good@example.com"},
        {"name": "Bad", "email": "nope"},
    ])

    status = client.get(f"/jobs/{job_id}").json()

    assert status["invalid"] == 1
    assert status["errors"][0]["row_index"] == 1
    assert status["errors"][0]["recipient_email"] == "nope"


def test_job_with_only_invalid_rows_fails_at_once_and_is_not_scheduled(client, scheduled):
    job_id = create_job(client, recipients=[{"name": "", "email": "x"}])

    assert scheduled == []                                     # nothing to generate
    assert client.get(f"/jobs/{job_id}").json()["status"] == "failed"


def test_names_are_trimmed(client):
    job_id = create_job(client, recipients=[{"name": "  Asha Rao  ", "email": "asha@example.com"}])

    row = client.get(f"/jobs/{job_id}/certificates").json()[0]
    assert row["recipient_name"] == "Asha Rao"
