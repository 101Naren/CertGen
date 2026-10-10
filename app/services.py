"""Business logic: validate rows, create jobs, summarise progress."""
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Certificate, CertStatus, Job, JobStatus
from app.schemas import JobCreate, RecipientIn


def _format_errors(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        field = ".".join(str(p) for p in err["loc"]) or "row"
        parts.append(f"{field}: {err['msg']}")
    return "; ".join(parts)


def _text_or_none(value: Any, limit: int) -> str | None:
    """Keep whatever string the client sent so the error can be traced to a person."""
    return value[:limit] if isinstance(value, str) else None


def validate_row(raw: Any) -> tuple[RecipientIn | None, str | None]:
    """Return (recipient, None) if valid, else (None, error message)."""
    if not isinstance(raw, dict):
        return None, "row must be an object with 'name' and 'email'"
    try:
        return RecipientIn.model_validate(raw), None
    except ValidationError as exc:
        return None, _format_errors(exc)


def create_job(db: Session, payload: JobCreate) -> Job:
    """Store the job and one Certificate row per recipient (valid or not)."""
    job = Job(
        title=payload.title,
        issuer=payload.issuer,
        issue_date=payload.issue_date,
        total=len(payload.recipients),
    )
    db.add(job)
    db.flush()  # assigns job.id so the certificate rows can point to it

    valid_count = 0
    for index, raw in enumerate(payload.recipients):
        recipient, error = validate_row(raw)
        if recipient is not None:
            valid_count += 1
            db.add(Certificate(
                job_id=job.id, row_index=index,
                recipient_name=recipient.name, recipient_email=str(recipient.email),
                status=CertStatus.PENDING,
            ))
        else:
            raw_dict = raw if isinstance(raw, dict) else {}
            db.add(Certificate(
                job_id=job.id, row_index=index,
                recipient_name=_text_or_none(raw_dict.get("name"), 200),
                recipient_email=_text_or_none(raw_dict.get("email"), 320),
                status=CertStatus.INVALID, error=error,
            ))

    if valid_count == 0:
        # Nothing to generate: the job is already over.
        job.status = JobStatus.FAILED
        job.finished_at = datetime.now(timezone.utc)

    db.commit()
    return job


def count_by_status(db: Session, job_id: str) -> dict[str, int]:
    rows = db.execute(
        select(Certificate.status, func.count())
        .where(Certificate.job_id == job_id)
        .group_by(Certificate.status)
    ).all()
    counts = {s: 0 for s in (CertStatus.PENDING, CertStatus.GENERATED,
                             CertStatus.FAILED, CertStatus.INVALID)}
    counts.update({status: n for status, n in rows})
    return counts


def finalize_job(db: Session, job_id: str) -> None:
    """Set the job's final status from the certificate counts."""
    counts = count_by_status(db, job_id)
    job = db.get(Job, job_id)
    if counts[CertStatus.GENERATED] == 0:
        job.status = JobStatus.FAILED
    elif counts[CertStatus.FAILED] or counts[CertStatus.INVALID]:
        job.status = JobStatus.COMPLETED_WITH_ERRORS
    else:
        job.status = JobStatus.COMPLETED
    job.finished_at = datetime.now(timezone.utc)
    db.commit()


def job_summary(db: Session, job: Job) -> dict:
    """Everything GET /jobs/{id} returns."""
    counts = count_by_status(db, job.id)
    done = job.total - counts[CertStatus.PENDING]
    problems = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job.id,
               Certificate.status.in_([CertStatus.INVALID, CertStatus.FAILED]))
        .order_by(Certificate.row_index)
    ).all()
    return {
        "job_id": job.id,
        "title": job.title,
        "status": job.status,
        "total": job.total,
        "generated": counts[CertStatus.GENERATED],
        "failed": counts[CertStatus.FAILED],
        "invalid": counts[CertStatus.INVALID],
        "pending": counts[CertStatus.PENDING],
        "progress_percent": round(done * 100 / job.total) if job.total else 100,
        "errors": [
            {"row_index": c.row_index, "status": c.status,
             "recipient_name": c.recipient_name, "recipient_email": c.recipient_email,
             "error": c.error}
            for c in problems
        ],
    }
