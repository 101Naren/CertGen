"""The HTTP endpoints."""
import io
import re
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import services, worker
from app.config import settings
from app.database import get_db
from app.models import Certificate, CertStatus, Job, JobStatus
from app.schemas import (CertificateOut, CertStatusLiteral, JobCreate, JobCreated,
                         JobStatusOut)

router = APIRouter()


def _get_job_or_404(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return job


def _slug(name: str | None) -> str:
    """Safe file-name fragment made from a recipient name."""
    return re.sub(r"[^A-Za-z0-9]+", "_", name or "").strip("_")[:40] or "certificate"


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/jobs", response_model=JobCreated, status_code=status.HTTP_202_ACCEPTED)
def create_job(payload: JobCreate, db: Session = Depends(get_db)):
    """Accept a bulk request, store it, and generate in the background (202)."""
    job = services.create_job(db, payload)
    if job.status == JobStatus.PENDING:   # at least one valid row to work on
        worker.submit(job.id)
    return JobCreated(job_id=job.id, status=job.status, total=job.total)


@router.get("/jobs/{job_id}", response_model=JobStatusOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    """Progress, per-status counts and the list of invalid/failed rows."""
    return services.job_summary(db, _get_job_or_404(db, job_id))


@router.get("/jobs/{job_id}/certificates", response_model=list[CertificateOut])
def list_certificates(
    job_id: str,
    status_filter: CertStatusLiteral | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
):
    """Every recipient row of a job, optionally filtered by ?status=failed."""
    job = _get_job_or_404(db, job_id)
    query = select(Certificate).where(Certificate.job_id == job.id)
    if status_filter:
        query = query.where(Certificate.status == status_filter)
    return db.scalars(query.order_by(Certificate.row_index)).all()


@router.get("/certificates/{certificate_id}/download")
def download_certificate(certificate_id: str, db: Session = Depends(get_db)):
    """One certificate as a PDF."""
    cert = db.get(Certificate, certificate_id)
    if cert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "certificate not found")
    if cert.status != CertStatus.GENERATED:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"certificate is not available (status: {cert.status})")
    path = Path(settings.storage_dir) / cert.file_path
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "certificate file is missing")
    return FileResponse(path, media_type="application/pdf",
                        filename=f"certificate_{_slug(cert.recipient_name)}.pdf")


@router.get("/jobs/{job_id}/download")
def download_job(job_id: str, db: Session = Depends(get_db)):
    """All generated certificates of a finished job as one ZIP."""
    job = _get_job_or_404(db, job_id)
    if job.status in JobStatus.UNFINISHED:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "job is still running; try again when it has finished")
    certs = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job.id, Certificate.status == CertStatus.GENERATED)
        .order_by(Certificate.row_index)
    ).all()
    if not certs:
        raise HTTPException(status.HTTP_409_CONFLICT, "no certificates were generated")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for cert in certs:
            path = Path(settings.storage_dir) / cert.file_path
            if path.is_file():
                archive.write(path, f"{cert.row_index + 1:04d}_{_slug(cert.recipient_name)}.pdf")
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="certificates_{job.id}.zip"'},
    )
