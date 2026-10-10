# Bulk Certificate Generator

A backend API that accepts **one request containing many recipients**, generates a PDF certificate for each valid recipient from a single predefined template, and lets the client track progress and download the results.

**Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2 (SQLite by default; MySQL/PostgreSQL via one setting), ReportLab (PDF), pytest.

---

## 1. Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

No database server is needed: SQLite is used by default and the file `certgen.db` is created on first run.

### Optional configuration

Set environment variables (or put them in a `.env` file):

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./certgen.db` | Any SQLAlchemy URL, e.g. `mysql+pymysql://user:pass@localhost/certgen` (install `pymysql` too) |
| `STORAGE_DIR` | `storage` | Folder where PDFs are written |
| `MAX_RECIPIENTS` | `1000` | Maximum recipients per request |
| `WORKER_THREADS` | `4` | Size of the background thread pool |

## 2. Run the application

```bash
uvicorn app.main:app --reload
```

- API: http://127.0.0.1:8000
- **Simple form (optional):** http://127.0.0.1:8000/ — enter the title, issuer, date and paste `name, email` lines; it shows progress and gives a ZIP to download. It is a single HTML file (`app/static/index.html`) that calls the same API below.
- Interactive API docs (try every endpoint in the browser): http://127.0.0.1:8000/docs

## 3. Run the tests

```bash
pytest -q
```

Tests use a temporary database and storage folder, so they never touch real data.

## 4. Submit a certificate generation request

```bash
curl -X POST http://127.0.0.1:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Full-Stack Workshop 2026",
    "issuer": "Example Tech Club",
    "issue_date": "2026-10-10",
    "recipients": [
      {"name": "Asha Rao",    "email": "asha@example.com"},
      {"name": "",            "email": "not-an-email"},
      {"name": "Vikram Iyer", "email": "vikram@example.com"}
    ]
  }'
```

Response (HTTP **202 Accepted**; the work happens in the background):

```json
{ "job_id": "5f0c1c1e-...", "status": "pending", "total": 3 }
```

`issue_date` is optional (defaults to today).

### Check progress

```bash
curl http://127.0.0.1:8000/jobs/<job_id>
```

```json
{
  "job_id": "5f0c1c1e-...",
  "title": "Full-Stack Workshop 2026",
  "status": "completed_with_errors",
  "total": 3,
  "generated": 2,
  "failed": 0,
  "invalid": 1,
  "pending": 0,
  "progress_percent": 100,
  "errors": [
    {"row_index": 1, "status": "invalid", "recipient_name": "", "recipient_email": "not-an-email",
     "error": "name: String should have at least 1 character; email: value is not a valid email address: ..."}
  ]
}
```

Job `status` is one of `pending`, `processing`, `completed`, `completed_with_errors`, `failed`.
Each recipient row has a status of `pending`, `generated`, `failed` (generation crashed) or `invalid` (rejected by validation). `row_index` is the position in the submitted list, starting at 0.

## 5. Retrieve generated certificates

| Endpoint | Returns |
| --- | --- |
| `GET /jobs/{job_id}/certificates` | Every recipient row; filter with `?status=generated\|failed\|invalid\|pending` |
| `GET /certificates/{certificate_id}/download` | One certificate as a PDF (`409` if it was not generated) |
| `GET /jobs/{job_id}/download` | ZIP of all generated PDFs (`409` while the job is still running or if nothing was generated) |

```bash
# list the generated certificates and copy an id
curl "http://127.0.0.1:8000/jobs/<job_id>/certificates?status=generated"

# download one
curl -o certificate.pdf http://127.0.0.1:8000/certificates/<certificate_id>/download

# download everything
curl -o certificates.zip http://127.0.0.1:8000/jobs/<job_id>/download
```

Other responses: `404` unknown job or certificate, `422` malformed request.

---

## 6. Design decisions

### Processing: background thread pool (not synchronous, not Celery)

`POST /jobs` stores the job, returns `202` with a job ID, and hands the job to a `ThreadPoolExecutor`. The client then polls `GET /jobs/{id}`.

- **Why not synchronous:** hundreds of PDFs would hold the HTTP request open, risking timeouts and tying up the server for one client.
- **Why not Celery + Redis:** it needs extra services to install and run, which is more than this task needs. A thread pool uses only the standard library and the project runs with one command.
- **Trade-off:** the in-process queue is lost if the server stops. This is mitigated at startup: `requeue_unfinished()` re-submits jobs left `pending`/`processing`, and only rows still `pending` are processed, so finished certificates are never regenerated. For multiple servers or guaranteed retries, the next step would be Celery with Redis.

### Two layers of validation

1. **Request level** (`JobCreate`): title and issuer required, 1 to `MAX_RECIPIENTS` recipients. Failure returns `422` and nothing is stored.
2. **Row level** (`RecipientIn`): name present (max 100 characters) and a valid email. A bad row is stored as `invalid` with a readable error and **does not stop the other rows**.

If every row is invalid, the job is marked `failed` immediately and no background work is scheduled.

### Failure isolation

Each certificate is generated inside its own `try/except` and committed on its own. A failure marks only that row `failed` with the error message; the loop continues. The final job status is derived from the counts: `completed`, `completed_with_errors`, or `failed` (nothing generated).

### Storage

PDFs are written to `STORAGE_DIR/<job_id>/<certificate_id>.pdf`; the database stores only the relative path. File names use generated UUIDs, never user input, so a hostile name cannot escape the folder. Files are written to a temporary name and renamed, so a crash cannot leave a half-written PDF. ZIP entry names use a sanitised version of the recipient name.

### Database sessions and threads

A SQLAlchemy session is not thread-safe. The request handler uses one session per request; each background job opens its own and closes it in a `finally`.

### Progress is derived, not stored

`progress_percent` and the counts are computed from the certificate rows each time, so they cannot drift out of sync with reality.

### Certificate template

One function (`app/pdf.py`) draws a landscape A4 certificate: recipient name, event title, issuer, issue date and a certificate ID. Long names are shrunk to fit.

**Known limitation:** the built-in PDF fonts cover Western European characters only. A name outside that range (for example Tamil or Chinese) is rejected with a clear error and that row is marked `failed`, instead of printing garbage. To support such names, register a Unicode TTF font with ReportLab (`pdfmetrics.registerFont(TTFont(...))`) in `pdf.py` and remove the `_check_printable` check.

## 7. Project layout

```text
app/
  main.py       app creation, startup (create tables, requeue unfinished jobs)
  config.py     settings from environment variables
  database.py   engine, sessions, get_db dependency
  models.py     Job and Certificate tables, status constants
  schemas.py    request/response models and validation rules
  services.py   row validation, job creation, status summary
  worker.py     thread pool and the generation loop
  pdf.py        the certificate template
  routes.py     HTTP endpoints
  static/index.html   optional one-page form served at /
tests/          pytest suite (creation, validation, generation, status, failures, downloads)
```

## 8. What would change for production

Celery + Redis (or another real queue) for durable jobs; PostgreSQL with Alembic migrations; object storage (e.g. S3) for PDFs; authentication and rate limiting; a Unicode font for non-Latin names; expiry of old files; pagination on the listing endpoint.
