"""Multi-format compliance reports: HTML (reports.py), CSV, PDF and DOCX.

CSV, PDF and DOCX are all stdlib — no reportlab or python-docx. The PDF is
a minimal hand-written text PDF; the DOCX is assembled as a zip of the
OOXML parts. All three are driven from the same tabular report data so the
numbers agree with the HTML report.
"""
from __future__ import annotations

import csv
import io
import time
import zipfile
from datetime import datetime, timezone

from .gitstore import GitStore
from .models import AppConfig
from .policy import check_all, severity_rank
from .runstore import RunStore


def report_rows(
    config: AppConfig, store: GitStore, runstore: RunStore,
    now: float | None = None,
) -> tuple[list[str], list[list[str]]]:
    """Return (header, rows) of the per-device compliance summary."""
    now = now if now is not None else time.time()
    header = ["device", "driver", "last_success_days", "consecutive_failures",
              "last_rehearsal_days", "has_baseline"]
    rows = []
    for device in config.all_devices():
        status = runstore.status(device.qualified_name)
        last_days = (
            "" if status.last_success is None
            else f"{(now - status.last_success) / 86400:.1f}"
        )
        reh = runstore.last_rehearsal(device.qualified_name)
        reh_days = (
            "" if not reh else f"{(now - reh['at']) / 86400:.1f}"
        )
        rows.append([
            device.qualified_name, device.driver, last_days,
            str(status.consecutive_failures),
            reh_days,
            "yes" if runstore.get_baseline(device.qualified_name) else "no",
        ])
    return header, rows


def policy_rows(config: AppConfig, store: GitStore) -> list[list[str]]:
    findings = check_all(config, store)
    flat = [f for g in findings.values() for f in g]
    flat.sort(key=lambda f: severity_rank(f.severity), reverse=True)
    return [[f.device, f.severity, f.rule_id, f.description] for f in flat]


def to_csv(config, store, runstore, now=None) -> bytes:
    header, rows = report_rows(config, store, runstore, now)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    writer.writerow([])
    writer.writerow(["policy_device", "severity", "rule", "description"])
    writer.writerows(policy_rows(config, store))
    return buf.getvalue().encode()


# ------------------------------------------------------------------- PDF

def _pdf_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def to_pdf(config, store, runstore, now=None) -> bytes:
    """A minimal single-stream PDF: a monospaced text dump of the report.
    Enough for a signable, printable, archivable artifact without a PDF
    library. Long reports paginate every ~60 lines."""
    generated = datetime.fromtimestamp(
        now or time.time(), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header, rows = report_rows(config, store, runstore, now)
    lines = [
        "OTITbup compliance report",
        f"Generated {generated}",
        "",
        "  ".join(header),
        "-" * 78,
    ]
    lines += ["  ".join(r) for r in rows]
    lines += ["", "Policy findings:", "-" * 78]
    lines += ["  ".join(p) for p in policy_rows(config, store)] or ["(none)"]

    # Paginate.
    pages: list[list[str]] = []
    for i in range(0, len(lines), 58):
        pages.append(lines[i:i + 58])

    objects: list[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)

    # 1 catalog, 2 pages tree, font, then per page: content + page.
    font_num = None
    page_obj_nums = []
    content_streams = []
    for page_lines in pages:
        text = "BT /F1 10 Tf 50 780 Td 12 TL\n"
        for ln in page_lines:
            text += f"({_pdf_escape(ln[:110])}) Tj T*\n"
        text += "ET"
        content_streams.append(text.encode("latin-1", "replace"))

    # Build object list with correct numbering.
    # Reserve: obj1 catalog, obj2 pages, obj3 font, then content+page pairs.
    catalog_num = 1
    pages_num = 2
    font_num = 3
    next_num = 4
    body_objs: dict[int, bytes] = {}
    for content in content_streams:
        c_num = next_num
        next_num += 1
        p_num = next_num
        next_num += 1
        page_obj_nums.append(p_num)
        body_objs[c_num] = (
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
            + content + b"\nendstream"
        )
        body_objs[p_num] = (
            b"<< /Type /Page /Parent " + str(pages_num).encode()
            + b" 0 R /MediaBox [0 0 595 842] /Contents "
            + str(c_num).encode() + b" 0 R /Resources << /Font << /F1 "
            + str(font_num).encode() + b" 0 R >> >> >>"
        )
    kids = b" ".join(str(n).encode() + b" 0 R" for n in page_obj_nums)
    body_objs[catalog_num] = (
        b"<< /Type /Catalog /Pages " + str(pages_num).encode() + b" 0 R >>")
    body_objs[pages_num] = (
        b"<< /Type /Pages /Kids [" + kids + b"] /Count "
        + str(len(page_obj_nums)).encode() + b" >>")
    body_objs[font_num] = (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(body_objs):
        offsets[num] = out.tell()
        out.write(str(num).encode() + b" 0 obj\n" + body_objs[num]
                  + b"\nendobj\n")
    xref_pos = out.tell()
    count = len(body_objs) + 1
    out.write(b"xref\n0 " + str(count).encode() + b"\n")
    out.write(b"0000000000 65535 f \n")
    for num in range(1, count):
        out.write(f"{offsets[num]:010d} 00000 n \n".encode())
    out.write(b"trailer\n<< /Size " + str(count).encode()
              + b" /Root 1 0 R >>\nstartxref\n"
              + str(xref_pos).encode() + b"\n%%EOF")
    return out.getvalue()


# ------------------------------------------------------------------ DOCX

def _docx_xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def to_docx(config, store, runstore, now=None) -> bytes:
    """Assemble a minimal but valid .docx (OOXML) as a zip of parts."""
    generated = datetime.fromtimestamp(
        now or time.time(), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header, rows = report_rows(config, store, runstore, now)

    def para(text, bold=False, size=22):
        rpr = "<w:rPr>" + ("<w:b/>" if bold else "") \
            + f"<w:sz w:val='{size}'/></w:rPr>"
        return (f"<w:p><w:pPr>{rpr}</w:pPr><w:r>{rpr}"
                f"<w:t xml:space='preserve'>{_docx_xml_escape(text)}"
                "</w:t></w:r></w:p>")

    body = para("OTITbup compliance report", bold=True, size=32)
    body += para(f"Generated {generated}", size=18)
    body += para("  ".join(header), bold=True)
    for r in rows:
        body += para("  ".join(r))
    body += para("Policy findings", bold=True, size=26)
    prows = policy_rows(config, store)
    if prows:
        for p in prows:
            body += para("  ".join(p))
    else:
        body += para("(none)")

    document = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<w:document xmlns:w='http://schemas.openxmlformats.org/"
        "wordprocessingml/2006/main'><w:body>" + body
        + "<w:sectPr/></w:body></w:document>"
    )
    content_types = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<Types xmlns='http://schemas.openxmlformats.org/package/2006/"
        "content-types'>"
        "<Default Extension='rels' ContentType='application/vnd.openxmlformats"
        "-package.relationships+xml'/>"
        "<Default Extension='xml' ContentType='application/xml'/>"
        "<Override PartName='/word/document.xml' ContentType='application/vnd."
        "openxmlformats-officedocument.wordprocessingml.document.main+xml'/>"
        "</Types>"
    )
    rels = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/"
        "relationships'><Relationship Id='rId1' Type='http://schemas."
        "openxmlformats.org/officeDocument/2006/relationships/officeDocument' "
        "Target='word/document.xml'/></Relationships>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


FORMATS = {
    "csv": (to_csv, "text/csv", "csv"),
    "pdf": (to_pdf, "application/pdf", "pdf"),
    "docx": (to_docx,
             "application/vnd.openxmlformats-officedocument."
             "wordprocessingml.document", "docx"),
}


def render(fmt: str, config, store, runstore, now=None) -> tuple[bytes, str, str]:
    """Return (bytes, content_type, extension) for a non-HTML format."""
    if fmt not in FORMATS:
        raise ValueError(f"unknown report format: {fmt}")
    func, content_type, ext = FORMATS[fmt]
    return func(config, store, runstore, now), content_type, ext
