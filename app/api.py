import io
import re
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import services
from app.models import Certificate, CertStatus, Job, JobStatus
from app.schemas import CertificateList, CertificateOut, JobCreate, JobOut

router = APIRouter()


def get_db(request: Request):
    with request.app.state.session_factory() as db:
        yield db


def _get_job(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


def _cert_out(c: Certificate) -> CertificateOut:
    ok = c.status == CertStatus.SUCCEEDED.value
    return CertificateOut(
        id=c.id, position=c.position, recipient_name=c.recipient_name, recipient_email=c.recipient_email,
        status=c.status, error=c.error, download_url=f"/certificates/{c.id}/download" if ok else None,
    )


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/jobs", response_model=JobOut, status_code=202)
def create_job(payload: JobCreate, background: BackgroundTasks, request: Request, db: Session = Depends(get_db)):
    """Accept a bulk request. Returns immediately; generation continues in the background."""
    state = request.app.state
    if len(payload.recipients) > state.settings.max_recipients_per_job:
        raise HTTPException(422, f"A job can contain at most {state.settings.max_recipients_per_job} recipients")
    job = services.create_job(db, payload)
    if job.status == JobStatus.PENDING.value:
        background.add_task(services.process_job, state.session_factory, state.settings.certificates_dir, job.id)
    return services.job_summary(db, job)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    return services.job_summary(db, _get_job(db, job_id))


@router.get("/jobs/{job_id}/certificates", response_model=CertificateList)
def list_certificates(
    job_id: str,
    status: Optional[CertStatus] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    _get_job(db, job_id)
    query = select(Certificate).where(Certificate.job_id == job_id)
    if status:
        query = query.where(Certificate.status == status.value)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(Certificate.position).limit(limit).offset(offset)).all()
    return CertificateList(total=total, limit=limit, offset=offset, items=[_cert_out(c) for c in rows])


@router.get("/jobs/{job_id}/certificates/download")
def download_all(job_id: str, db: Session = Depends(get_db)):
    """ZIP of every successfully generated certificate in the job."""
    job = _get_job(db, job_id)
    if job.status in (JobStatus.PENDING.value, JobStatus.PROCESSING.value):
        raise HTTPException(409, "Job is still being processed; check GET /jobs/{id} for progress")
    certs = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job_id, Certificate.status == CertStatus.SUCCEEDED.value)
        .order_by(Certificate.position)
    ).all()
    buf, added = io.BytesIO(), 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for c in certs:
            p = Path(c.file_path or "")
            if p.is_file():
                slug = re.sub(r"[^A-Za-z0-9]+", "_", c.recipient_name or "").strip("_") or "certificate"
                zf.write(p, arcname=f"{c.position + 1:04d}_{slug}.pdf")
                added += 1
    if not added:
        raise HTTPException(404, "No generated certificates available for this job")
    return Response(
        buf.getvalue(), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="certificates_{job_id}.zip"'},
    )


@router.get("/certificates/{certificate_id}/download")
def download_certificate(certificate_id: str, db: Session = Depends(get_db)):
    cert = db.get(Certificate, certificate_id)
    if cert is None:
        raise HTTPException(404, "Certificate not found")
    if cert.status != CertStatus.SUCCEEDED.value or not cert.file_path or not Path(cert.file_path).is_file():
        raise HTTPException(409, f"Certificate is not available (status: {cert.status})")
    return FileResponse(cert.file_path, media_type="application/pdf", filename=f"certificate_{certificate_id}.pdf")
