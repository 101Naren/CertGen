"""The one predefined certificate template.

`render_certificate` draws a landscape A4 PDF with ReportLab. Every certificate
has the same layout; only the recipient-specific text changes.

Known limit: the built-in PDF fonts only cover Western European characters.
Text outside that range (for example Tamil or Chinese names) is rejected with a
clear error instead of printing garbage, so that row is marked "failed" and the
rest of the job carries on. See README for the upgrade path (register a TTF).
"""
import os
from datetime import date
from pathlib import Path

from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = landscape(A4)
MAX_TEXT_WIDTH = PAGE_W - 160


def _check_printable(label: str, text: str) -> None:
    """Raise if the built-in font cannot print this text."""
    try:
        text.encode("cp1252")
    except UnicodeEncodeError as exc:
        raise ValueError(
            f"{label} contains characters the certificate font cannot print"
        ) from exc


def _fit(text: str, font: str, start_size: int, min_size: int = 10) -> int:
    """Largest font size (<= start_size) at which the text fits the page width."""
    size = start_size
    while size > min_size and stringWidth(text, font, size) > MAX_TEXT_WIDTH:
        size -= 1
    if stringWidth(text, font, size) > MAX_TEXT_WIDTH:
        raise ValueError("text is too long to fit on the certificate")
    return size


def _centered(c: canvas.Canvas, y: float, text: str, font: str, size: int) -> None:
    c.setFont(font, size)
    c.drawCentredString(PAGE_W / 2, y, text)


def render_certificate(
    *,
    name: str,
    title: str,
    issuer: str,
    issue_date: date,
    certificate_id: str,
    path: Path,
) -> None:
    """Write one certificate PDF to `path`."""
    for label, text in (("name", name), ("title", title), ("issuer", issuer)):
        _check_printable(label, text)

    name_size = _fit(name, "Times-BoldItalic", 44, 14)
    title_size = _fit(title, "Helvetica-Bold", 24, 12)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temporary file first, then rename, so a crash can never
    # leave a half-written PDF at the final path.
    tmp_path = path.with_suffix(".pdf.tmp")

    c = canvas.Canvas(str(tmp_path), pagesize=(PAGE_W, PAGE_H))
    c.setTitle(f"Certificate - {name}")

    # Double border
    c.setLineWidth(3)
    c.rect(30, 30, PAGE_W - 60, PAGE_H - 60)
    c.setLineWidth(1)
    c.rect(42, 42, PAGE_W - 84, PAGE_H - 84)

    _centered(c, PAGE_H - 120, "CERTIFICATE", "Helvetica-Bold", 40)
    _centered(c, PAGE_H - 150, "OF COMPLETION", "Helvetica", 16)
    _centered(c, PAGE_H - 215, "This is to certify that", "Times-Italic", 18)
    _centered(c, PAGE_H - 275, name, "Times-BoldItalic", name_size)
    c.line(PAGE_W / 2 - 220, PAGE_H - 288, PAGE_W / 2 + 220, PAGE_H - 288)
    _centered(c, PAGE_H - 330, "has successfully completed", "Times-Italic", 18)
    _centered(c, PAGE_H - 372, title, "Helvetica-Bold", title_size)

    _centered(c, 120, f"Issued by {issuer}", "Helvetica", 14)
    _centered(c, 98, issue_date.strftime("%d %B %Y"), "Helvetica", 12)
    _centered(c, 62, f"Certificate ID: {certificate_id}", "Helvetica", 8)

    c.showPage()
    c.save()
    os.replace(tmp_path, path)
