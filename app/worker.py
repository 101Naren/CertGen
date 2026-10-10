"""Background generation.

The API saves a job and returns immediately; `submit` hands the job to a small
thread pool. Each job runs in its own thread with its own database session.

Failure isolation: every certificate is generated inside its own try/except and
committed on its own, so one bad row can never stop the others.
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app import database, pdf, services
from app.config import settings
from app.models import Certificate, CertStatus, Job, JobStatus

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=settings.worker_threads,
                               thread_name_prefix="certgen")


def submit(job_id: str) -> None:
    """Queue a job for background processing."""
    _executor.submit(process_job, job_id)


def _generate_one(db, job: Job, cert_id: str) -> None:
    cert = db.get(Certificate, cert_id)
    relative_path = f"{job.id}/{cert.id}.pdf"
    try:
        pdf.render_certificate(
            name=cert.recipient_name,
            title=job.title,
            issuer=job.issuer,
            issue_date=job.issue_date,
            certificate_id=cert.id,
            path=Path(settings.storage_dir) / relative_path,
        )
        cert.status = CertStatus.GENERATED
        cert.file_path = relative_path
        cert.error = None
        cert.generated_at = datetime.now(timezone.utc)
    except Exception as exc:  # deliberately broad: any failure stays on this row
        logger.warning("certificate %s failed: %s", cert_id, exc)
        cert.status = CertStatus.FAILED
        cert.error = f"{type(exc).__name__}: {exc}"[:500]
    db.commit()


def process_job(job_id: str) -> None:
    """Generate every pending certificate of one job, then set its final status."""
    db = database.SessionLocal()   # this thread's own session
    try:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.status = JobStatus.PROCESSING
        db.commit()

        pending_ids = db.scalars(
            select(Certificate.id)
            .where(Certificate.job_id == job_id, Certificate.status == CertStatus.PENDING)
            .order_by(Certificate.row_index)
        ).all()
        for cert_id in pending_ids:
            _generate_one(db, job, cert_id)

        services.finalize_job(db, job_id)
    except Exception:
        # Something outside a single certificate broke (e.g. the database).
        logger.exception("job %s crashed", job_id)
        db.rollback()
        try:
            job = db.get(Job, job_id)
            if job is not None:
                job.status = JobStatus.FAILED
                job.finished_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            logger.exception("could not mark job %s as failed", job_id)
    finally:
        db.close()


def requeue_unfinished() -> int:
    """After a restart, pick up jobs that were queued or running when we stopped.

    Certificates already generated are skipped because only 'pending' rows are processed.
    """
    db = database.SessionLocal()
    try:
        ids = db.scalars(select(Job.id).where(Job.status.in_(JobStatus.UNFINISHED))).all()
    finally:
        db.close()
    for job_id in ids:
        submit(job_id)
    return len(ids)
