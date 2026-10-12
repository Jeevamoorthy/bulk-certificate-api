import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        certificates_dir=str(tmp_path / "certs"),
        max_recipients_per_job=50,
    )
    # TestClient runs FastAPI background tasks before returning, so jobs are finished after POST.
    with TestClient(create_app(settings)) as c:
        yield c


def make_payload(*names, **extra):
    return {"event_name": "PyCon Workshop", "recipients": [{"name": n} for n in names], **extra}


def submit(client, payload):
    """POST a job, then fetch its final state (the POST response is a snapshot taken before generation)."""
    created = client.post("/jobs", json=payload)
    assert created.status_code == 202
    return client.get(f"/jobs/{created.json()['id']}").json()
