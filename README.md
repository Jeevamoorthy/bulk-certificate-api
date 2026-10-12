# Bulk Certificate Generator API

A backend API that accepts **one request containing many recipients**, generates a PDF certificate for each from a single predefined template, tracks progress, and lets the client retrieve the results individually or as a ZIP.

**Stack:** Python 3.10+, FastAPI, SQLAlchemy 2.0 (SQLite by default, any SQL database via `DATABASE_URL`), ReportLab (PDF), pytest.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
```

## Run

```bash
uvicorn app.main:app --reload
```

Interactive docs (Swagger UI): <http://127.0.0.1:8000/docs>. Tables are created automatically on startup.

Optional environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./certificates.db` | Any SQLAlchemy URL (e.g. PostgreSQL) |
| `CERTIFICATES_DIR` | `./generated_certificates` | Where PDFs are written |
| `MAX_RECIPIENTS_PER_JOB` | `1000` | Upper bound per request |

Docker: `docker build -t certgen . && docker run -p 8000:8000 -v certdata:/data certgen`

## Test

```bash
pytest
```

## API

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/jobs` | Submit a bulk generation request (returns `202`) |
| `GET` | `/jobs/{job_id}` | Status, counts, progress %, list of failures |
| `GET` | `/jobs/{job_id}/certificates?status=&limit=&offset=` | Paginated per-certificate status |
| `GET` | `/jobs/{job_id}/certificates/download` | ZIP of all successfully generated PDFs |
| `GET` | `/certificates/{certificate_id}/download` | A single PDF |

### Submit a request

```bash
curl -X POST http://127.0.0.1:8000/jobs -H "Content-Type: application/json" -d '{
  "event_name": "Aereo Hackathon 2026",
  "issuer_name": "Aereo Club",
  "issue_date": "2026-10-12",
  "recipients": [
    {"name": "Asha Kumar", "email": "asha@example.com", "note": "with Distinction"},
    {"name": "Ravi Shankar"}
  ]
}'
```

`issuer_name` and `issue_date` are optional (defaults: "The Organizing Committee", today). Per recipient: `name` (required, 1-100 chars), `email` (optional, must be valid if given), `note` (optional, printed under the event name).

The response is a snapshot taken right after validation, with `"status": "pending"`:

```json
{"id": "3a5c74b8...", "status": "pending", "total": 3, "succeeded": 0, "failed": 1, "pending": 2,
 "progress_percent": 33.3,
 "failures": [{"position": 1, "recipient_name": "", "error": "name: String should have at least 1 character"}]}
```

### Check progress

```bash
curl http://127.0.0.1:8000/jobs/<job_id>
```

Job `status` values: `pending` -> `processing` -> `completed` (all succeeded) | `completed_with_errors` (some failed) | `failed` (none succeeded). `failures` lists the recipient `position` (index in the request), name and reason for each failed certificate (first 100; use the `status=failed` filter on the certificates endpoint for the full list).

### Retrieve certificates

```bash
curl http://127.0.0.1:8000/jobs/<job_id>/certificates                  # list, with download_url per certificate
curl -OJ http://127.0.0.1:8000/jobs/<job_id>/certificates/download     # ZIP of everything that succeeded
curl -OJ http://127.0.0.1:8000/certificates/<certificate_id>/download  # one PDF
```

The ZIP returns `409` while the job is still running, and only contains successful certificates (named `0001_Asha_Kumar.pdf`, where the number is the recipient's position in the request).

## Design decisions

**Background processing (not synchronous).** `POST /jobs` validates the request, stores the job, and returns `202` immediately; generation runs in a FastAPI `BackgroundTask` after the response is sent. A request with hundreds of recipients would otherwise hold the HTTP connection open and risk client/proxy timeouts, and the job/status model makes progress visible. Trade-off: background tasks run inside the web process, so a server restart mid-job leaves it in `processing`. For production I'd move `services.process_job` (which only needs a job id) to a queue worker such as Celery/RQ/Arq with the DB as the source of truth; nothing else would change.

**Per-recipient validation, not per-request.** Structural problems (missing `event_name`, empty or non-list `recipients`, too many recipients) reject the whole request with `422`. Problems with an individual recipient (blank name, bad email, not an object) don't: that row is stored as `failed` with a reason and every valid recipient still gets a certificate. If *every* recipient is invalid the job is immediately `failed`.

**Failure isolation.** Each certificate is rendered inside its own `try/except` and committed individually, so one failure (e.g. a name with characters the font can't render) is recorded on that row and the job continues. Committing per certificate also makes `progress_percent` accurate while a job runs. PDFs are written to a temp file and renamed, so a failed render never leaves a half-written file. If the whole loop aborts unexpectedly, remaining pending rows are marked failed so a job never gets stuck.

**Data model.** `jobs` (event details, status, timestamps) has a one-to-many relationship with `certificates` (position, recipient fields, status, error, file path, raw input). Counts and progress are always derived from certificate rows rather than stored, so they can't drift out of sync.

**Template.** One landscape A4 template drawn with ReportLab (border, title, recipient name auto-shrunk to fit, event, optional note, date, issuer, certificate ID). It's code, not an editable design, as the assignment specifies.

**Known limitations**
- The built-in PDF fonts only cover Latin (WinAnsi) characters. Names outside that range (e.g. Tamil, Devanagari) fail cleanly for that certificate with an explanatory error. Supporting them means registering a Unicode TTF font in `generator.py`.
- No authentication or rate limiting; the ZIP is built in memory (fine for the 1000-recipient cap).
- Jobs interrupted by a process restart are not resumed (see above).
