import io
import zipfile

import pytest
from pypdf import PdfReader

from app import services
from app.schemas import JobCreate
from tests.conftest import make_payload, submit


# ---------- Creating a job ----------

def test_create_job_returns_202_and_job_summary(client):
    r = client.post("/jobs", json=make_payload("Asha", "Ravi", "Meena"))
    assert r.status_code == 202
    body = r.json()
    assert body["total"] == 3
    assert body["id"]
    assert body["certificates_url"] == f"/jobs/{body['id']}/certificates"


def test_post_returns_before_generation_with_pending_status(client):
    r = client.post("/jobs", json=make_payload("Asha", "Ravi"))
    assert r.json()["status"] == "pending"
    assert r.json()["pending"] == 2


def test_unknown_job_is_404(client):
    assert client.get("/jobs/doesnotexist").status_code == 404


# ---------- Input validation ----------

@pytest.mark.parametrize(
    "payload",
    [
        {"recipients": [{"name": "A"}]},                          # missing event_name
        {"event_name": "X"},                                      # missing recipients
        {"event_name": "X", "recipients": []},                    # empty recipients
        {"event_name": "   ", "recipients": [{"name": "A"}]},     # blank event_name
        {"event_name": "X", "recipients": "Asha"},                # wrong type
    ],
)
def test_invalid_request_is_rejected_with_422(client, payload):
    assert client.post("/jobs", json=payload).status_code == 422


def test_too_many_recipients_rejected(client):
    r = client.post("/jobs", json=make_payload(*[f"P{i}" for i in range(51)]))
    assert r.status_code == 422


def test_invalid_recipients_are_reported_but_valid_ones_still_generated(client):
    payload = {
        "event_name": "PyCon",
        "recipients": [
            {"name": "Asha", "email": "asha@example.com"},
            {"name": ""},                                # empty name
            {"email": "no-name@example.com"},            # missing name
            {"name": "Ravi", "email": "not-an-email"},   # bad email
            "just a string",                             # not an object
            {"name": "Meena"},
        ],
    }
    body = submit(client, payload)
    assert body["status"] == "completed_with_errors"
    assert (body["total"], body["succeeded"], body["failed"]) == (6, 2, 4)
    assert [f["position"] for f in body["failures"]] == [1, 2, 3, 4]
    assert all(f["error"] for f in body["failures"])


def test_job_with_only_invalid_recipients_fails_immediately(client):
    body = submit(client, make_payload("", ""))
    assert body["status"] == "failed"
    assert body["succeeded"] == 0 and body["failed"] == 2


# ---------- Certificate generation ----------

def test_generated_certificate_is_a_pdf_containing_recipient_details(client):
    job = submit(client, make_payload("Asha Kumar", note="with Distinction"))
    cert = client.get(job["certificates_url"]).json()["items"][0]
    pdf = client.get(cert["download_url"])
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")
    text = PdfReader(io.BytesIO(pdf.content)).pages[0].extract_text()
    assert "Asha Kumar" in text and "PyCon Workshop" in text


def test_note_is_rendered_when_given(client):
    job = client.post("/jobs", json={
        "event_name": "PyCon", "recipients": [{"name": "Asha", "note": "with Distinction"}]}).json()
    cert = client.get(job["certificates_url"]).json()["items"][0]
    text = PdfReader(io.BytesIO(client.get(cert["download_url"]).content)).pages[0].extract_text()
    assert "with Distinction" in text


# ---------- Job status / progress ----------

def test_job_progress_goes_from_pending_to_completed(client):
    app = client.app
    with app.state.session_factory() as db:
        job = services.create_job(db, JobCreate(event_name="E", recipients=[{"name": "A"}, {"name": "B"}, {"name": ""}]))
        job_id = job.id

    before = client.get(f"/jobs/{job_id}").json()
    assert before["status"] == "pending"
    assert (before["pending"], before["failed"], before["progress_percent"]) == (2, 1, 33.3)

    services.process_job(app.state.session_factory, app.state.settings.certificates_dir, job_id)

    after = client.get(f"/jobs/{job_id}").json()
    assert after["status"] == "completed_with_errors"
    assert (after["pending"], after["succeeded"], after["failed"]) == (0, 2, 1)
    assert after["progress_percent"] == 100.0
    assert after["completed_at"] is not None


def test_fully_successful_job_is_completed(client):
    body = submit(client, make_payload("A", "B"))
    assert body["status"] == "completed" and body["failures"] == []


# ---------- Individual certificate failure ----------

def test_one_rendering_failure_does_not_stop_other_certificates(client, monkeypatch):
    real = services.render_certificate

    def flaky(path, **kwargs):
        if kwargs["recipient_name"] == "Boom":
            raise RuntimeError("renderer exploded")
        return real(path, **kwargs)

    monkeypatch.setattr(services, "render_certificate", flaky)
    body = submit(client, make_payload("Asha", "Boom", "Ravi"))

    assert body["status"] == "completed_with_errors"
    assert (body["succeeded"], body["failed"]) == (2, 1)
    assert body["failures"][0]["recipient_name"] == "Boom"
    assert "renderer exploded" in body["failures"][0]["error"]

    items = client.get(body["certificates_url"]).json()["items"]
    assert [i["status"] for i in items] == ["succeeded", "failed", "succeeded"]


def test_unsupported_characters_fail_only_that_certificate(client):
    body = submit(client, make_payload("Asha", "நிவேதா", "Ravi"))
    assert (body["succeeded"], body["failed"]) == (2, 1)
    assert "cannot render" in body["failures"][0]["error"]


# ---------- Retrieving certificates ----------

def test_list_certificates_with_filter_and_pagination(client):
    job = submit(client, make_payload("A", "", "C", "D"))
    url = job["certificates_url"]
    assert client.get(url).json()["total"] == 4
    failed = client.get(url, params={"status": "failed"}).json()
    assert failed["total"] == 1 and failed["items"][0]["download_url"] is None
    page = client.get(url, params={"limit": 2, "offset": 2}).json()
    assert [i["position"] for i in page["items"]] == [2, 3]


def test_download_all_returns_zip_of_successful_certificates_only(client):
    job = submit(client, make_payload("Asha", "", "Ravi"))
    r = client.get(job["download_all_url"])
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert sorted(zf.namelist()) == ["0001_Asha.pdf", "0003_Ravi.pdf"]
        assert all(zf.read(n).startswith(b"%PDF") for n in zf.namelist())


def test_download_of_failed_certificate_is_409(client):
    job = submit(client, make_payload("Asha", ""))
    failed = client.get(job["certificates_url"], params={"status": "failed"}).json()["items"][0]
    assert client.get(f"/certificates/{failed['id']}/download").status_code == 409


def test_download_unknown_certificate_is_404(client):
    assert client.get("/certificates/nope/download").status_code == 404


def test_download_all_while_job_pending_is_409(client):
    with client.app.state.session_factory() as db:
        job_id = services.create_job(db, JobCreate(event_name="E", recipients=[{"name": "A"}])).id
    assert client.get(f"/jobs/{job_id}/certificates/download").status_code == 409


def test_download_all_when_nothing_generated_is_404(client):
    job = submit(client, make_payload(""))
    assert client.get(job["download_all_url"]).status_code == 404
