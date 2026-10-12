"""Renders a single certificate PDF from the one predefined template."""
import os
from datetime import date
from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = landscape(A4)
NAVY = colors.HexColor("#1f3a5f")
GOLD = colors.HexColor("#b8902f")


def _check_encodable(text: str, field: str) -> None:
    # Built-in PDF fonts only cover Latin-1/WinAnsi. Fail clearly instead of printing garbage.
    try:
        text.encode("cp1252")
    except UnicodeEncodeError:
        raise ValueError(f"{field} contains characters the certificate font cannot render")


def _fit(text: str, font: str, size: float, max_width: float, min_size: float = 14) -> float:
    while size > min_size and stringWidth(text, font, size) > max_width:
        size -= 1
    return size


def render_certificate(
    path: Path,
    *,
    recipient_name: str,
    event_name: str,
    issuer_name: str,
    issue_date: date,
    certificate_id: str,
    note: Optional[str] = None,
) -> None:
    for label, value in (("name", recipient_name), ("event name", event_name), ("issuer name", issuer_name), ("note", note or "")):
        _check_encodable(value, label)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")  # write-then-rename so a crash never leaves a half-written PDF
    cx, max_w = PAGE_W / 2, PAGE_W - 160

    c = canvas.Canvas(str(tmp), pagesize=(PAGE_W, PAGE_H))
    c.setTitle(f"Certificate - {recipient_name}")

    # Double border
    c.setStrokeColor(NAVY); c.setLineWidth(4); c.rect(25, 25, PAGE_W - 50, PAGE_H - 50)
    c.setStrokeColor(GOLD); c.setLineWidth(1.5); c.rect(35, 35, PAGE_W - 70, PAGE_H - 70)

    c.setFillColor(NAVY)
    c.setFont("Times-Bold", 44); c.drawCentredString(cx, PAGE_H - 120, "CERTIFICATE")
    c.setFillColor(GOLD)
    c.setFont("Times-Roman", 18); c.drawCentredString(cx, PAGE_H - 150, "OF PARTICIPATION")

    c.setFillColor(colors.black)
    c.setFont("Helvetica", 14); c.drawCentredString(cx, PAGE_H - 215, "This is to certify that")

    size = _fit(recipient_name, "Times-BoldItalic", 40, max_w)
    c.setFillColor(NAVY); c.setFont("Times-BoldItalic", size)
    c.drawCentredString(cx, PAGE_H - 270, recipient_name)
    c.setStrokeColor(GOLD); c.setLineWidth(1)
    c.line(cx - 200, PAGE_H - 282, cx + 200, PAGE_H - 282)

    c.setFillColor(colors.black); c.setFont("Helvetica", 14)
    c.drawCentredString(cx, PAGE_H - 320, "has successfully participated in")
    size = _fit(event_name, "Helvetica-Bold", 24, max_w)
    c.setFont("Helvetica-Bold", size); c.drawCentredString(cx, PAGE_H - 355, event_name)

    if note:
        c.setFont("Helvetica-Oblique", 14); c.drawCentredString(cx, PAGE_H - 385, note)

    # Footer: date, issuer, certificate id
    c.setFont("Helvetica", 12)
    c.drawString(90, 100, f"Date: {issue_date.strftime('%d %B %Y')}")
    c.line(PAGE_W - 290, 105, PAGE_W - 90, 105)
    c.drawCentredString(PAGE_W - 190, 88, issuer_name[:60])
    c.setFont("Helvetica", 8); c.setFillColor(colors.grey)
    c.drawCentredString(cx, 50, f"Certificate ID: {certificate_id}")
    c.save()
    os.replace(tmp, path)
