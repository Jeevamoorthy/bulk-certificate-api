import logging
from collections import Counter
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.generator import render_certificate
from app.models import Certificate, CertStatus, Job, JobStatus, utcnow
from app.schemas import JobCreate, RecipientData

logger = logging.getLogger(__name__)
MAX_FAILURES_IN_JOB_RESPONSE = 100


def _format_validation_error(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'recipient'}: {e['msg']}" for e in exc.errors())


def create_job(db: Session, payload: JobCreate) -> Job:
    """Persist the job and one Certificate row per recipient.

    Each recipient is validated individually. Invalid ones are stored as FAILED right away
    (with the reason); valid ones are PENDING and picked up by process_job.
    """
    job = Job(event_name=payload.event_name, issuer_name=payload.issuer_name, issue_date=payload.issue_date)
    db.add(job)
    valid = 0
    for position, raw in enumerate(payload.recipients):
        cert = Certificate(job=job, position=position, input_data=raw if isinstance(raw, dict) else {"value": raw})
        db.add(cert)
        try:
            if not isinstance(raw, dict):
                raise ValueError("recipient must be a JSON object")
            data = RecipientData.model_validate(raw)
        except ValidationError as exc:
            cert.status, cert.error = CertStatus.FAILED.value, _format_validation_error(exc)
            name = raw.get("name") if isinstance(raw, dict) else None
            cert.recipient_name = name[:200] if isinstance(name, str) else None
        except ValueError as exc:
            cert.status, cert.error = CertStatus.FAILED.value, str(exc)
        else:
            valid += 1
            cert.recipient_name, cert.recipient_email, cert.note = data.name, data.email, data.note
    if valid:
        job.status = JobStatus.PENDING.value
    else:
        job.status, job.completed_at = JobStatus.FAILED.value, utcnow()
    db.commit()
    return job


def process_job(session_factory, certificates_dir: str, job_id: str) -> None:
    """Generate every PENDING certificate of a job. Runs in a background task."""
    with session_factory() as db:
        job = db.get(Job, job_id)
        if job is None or job.status != JobStatus.PENDING.value:
            return
        job.status = JobStatus.PROCESSING.value
        db.commit()

        try:
            cert_ids = db.scalars(
                select(Certificate.id)
                .where(Certificate.job_id == job_id, Certificate.status == CertStatus.PENDING.value)
                .order_by(Certificate.position)
            ).all()
            for cert_id in cert_ids:
                cert = db.get(Certificate, cert_id)
                path = Path(certificates_dir) / job_id / f"{cert_id}.pdf"
                try:
                    render_certificate(
                        path,
                        recipient_name=cert.recipient_name,
                        event_name=job.event_name,
                        issuer_name=job.issuer_name,
                        issue_date=job.issue_date,
                        certificate_id=cert_id,
                        note=cert.note,
                    )
                    cert.status, cert.file_path = CertStatus.SUCCEEDED.value, str(path)
                except Exception as exc:  # one bad certificate must not stop the rest
                    logger.warning("Certificate %s (job %s) failed: %s", cert_id, job_id, exc)
                    cert.status, cert.error = CertStatus.FAILED.value, f"Generation failed: {exc}"[:500]
                db.commit()  # commit per certificate so progress is visible while the job runs
        except Exception:
            logger.exception("Job %s aborted unexpectedly", job_id)
            db.rollback()
            db.execute(
                update(Certificate)
                .where(Certificate.job_id == job_id, Certificate.status == CertStatus.PENDING.value)
                .values(status=CertStatus.FAILED.value, error="Job aborted unexpectedly")
            )
            db.commit()

        counts = status_counts(db, job_id)
        if counts[CertStatus.SUCCEEDED.value] == 0:
            job.status = JobStatus.FAILED.value
        elif counts[CertStatus.FAILED.value]:
            job.status = JobStatus.COMPLETED_WITH_ERRORS.value
        else:
            job.status = JobStatus.COMPLETED.value
        job.completed_at = utcnow()
        db.commit()


def status_counts(db: Session, job_id: str) -> Counter:
    rows = db.execute(
        select(Certificate.status, func.count()).where(Certificate.job_id == job_id).group_by(Certificate.status)
    ).all()
    return Counter({status: n for status, n in rows})


def job_summary(db: Session, job: Job) -> dict:
    counts = status_counts(db, job.id)
    total = sum(counts.values())
    succeeded, failed, pending = (counts[s.value] for s in (CertStatus.SUCCEEDED, CertStatus.FAILED, CertStatus.PENDING))
    failures = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job.id, Certificate.status == CertStatus.FAILED.value)
        .order_by(Certificate.position)
        .limit(MAX_FAILURES_IN_JOB_RESPONSE + 1)
    ).all()
    return {
        "id": job.id,
        "event_name": job.event_name,
        "status": job.status,
        "total": total,
        "succeeded": succeeded,
        "failed": failed,
        "pending": pending,
        "progress_percent": round((succeeded + failed) / total * 100, 1) if total else 100.0,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
        "failures": [
            {"position": c.position, "recipient_name": c.recipient_name, "error": c.error}
            for c in failures[:MAX_FAILURES_IN_JOB_RESPONSE]
        ],
        "failures_truncated": len(failures) > MAX_FAILURES_IN_JOB_RESPONSE,
        "certificates_url": f"/jobs/{job.id}/certificates",
        "download_all_url": f"/jobs/{job.id}/certificates/download",
    }
