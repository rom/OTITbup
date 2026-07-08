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


# Report palette (0-1 RGB) matching the web UI charts.
_DARK = (0.08, 0.09, 0.11)
_OK = (0.106, 0.478, 0.184)
_CHANGE = (0.043, 0.341, 0.816)
_FAIL = (0.702, 0.149, 0.118)
_WARN = (0.698, 0.416, 0.0)
_MUTED = (0.596, 0.635, 0.678)
_LIGHT = (0.955, 0.965, 0.973)
_WHITE = (1, 1, 1)


def _status_counts(config, runstore, now):
    covered = never = stale = failing = healthy = 0
    for device in config.all_devices():
        st = runstore.status(device.qualified_name)
        if st.last_success is not None:
            covered += 1
        if st.last_success is None:
            never += 1
        elif st.consecutive_failures > 0:
            failing += 1
        elif now - st.last_success > 7 * 86400:
            stale += 1
        else:
            healthy += 1
    return {"healthy": healthy, "stale": stale, "never": never,
            "failing": failing, "covered": covered,
            "total": len(config.all_devices())}


def to_pdf(config, store, runstore, now=None) -> bytes:
    """A richly formatted PDF: title banner, KPI tiles, a status bar chart,
    and shaded tables — drawn with vector graphics (no PDF library)."""
    from .pdfcanvas import PAGE_H, PAGE_W, PDFCanvas
    now = now or time.time()
    generated = datetime.fromtimestamp(now, timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC")
    header, rows = report_rows(config, store, runstore, now)
    prows = policy_rows(config, store)
    counts = _status_counts(config, runstore, now)

    c = PDFCanvas()
    margin = 45
    y = PAGE_H

    def banner(subtitle):
        nonlocal y
        c.rect(0, PAGE_H - 70, PAGE_W, 70, fill=_DARK)
        c.text(margin, PAGE_H - 40, "OTITbup compliance report",
               size=20, font="bold", color=_WHITE)
        c.text(margin, PAGE_H - 58, subtitle, size=10, color=(0.8, 0.85, 0.9))
        y = PAGE_H - 90

    banner(f"Generated {generated}")

    # KPI tiles.
    tiles = [
        ("Covered", f"{counts['covered']}/{counts['total']}", _OK),
        ("Stale >7d", str(counts["stale"]),
         _WARN if counts["stale"] else _OK),
        ("Never", str(counts["never"]),
         _FAIL if counts["never"] else _OK),
        ("Failing", str(counts["failing"]),
         _FAIL if counts["failing"] else _OK),
    ]
    tile_w = (PAGE_W - 2 * margin - 3 * 12) / 4
    for i, (label, value, color) in enumerate(tiles):
        tx = margin + i * (tile_w + 12)
        c.rect(tx, y - 56, tile_w, 56, fill=_LIGHT, stroke=(0.87, 0.89, 0.91))
        c.rect(tx, y - 56, 5, 56, fill=color)          # colour spine
        c.text(tx + 14, y - 26, value, size=20, font="bold", color=color)
        c.text(tx + 14, y - 46, label, size=9, color=_MUTED)
    y -= 78

    # Status bar chart (drawn as vector bars).
    c.text(margin, y, "Device status", size=12, font="bold")
    y -= 14
    chart_h = 90
    chart_w = PAGE_W - 2 * margin
    bars = [("healthy", counts["healthy"], _OK), ("stale", counts["stale"], _WARN),
            ("never", counts["never"], _MUTED), ("failing", counts["failing"], _FAIL)]
    peak = max((v for _l, v, _c in bars), default=0) or 1
    bar_w = chart_w / len(bars)
    base = y - chart_h
    c.line(margin, base, margin + chart_w, base, color=(0.8, 0.82, 0.85))
    for i, (label, val, color) in enumerate(bars):
        bh = (chart_h - 16) * (val / peak)
        bx = margin + i * bar_w + bar_w * 0.2
        c.rect(bx, base, bar_w * 0.6, bh, fill=color)
        c.text_centered(bx + bar_w * 0.3, base + bh + 4, str(val),
                        size=10, font="bold", color=color)
        c.text_centered(bx + bar_w * 0.3, base - 12, label, size=9,
                        color=_MUTED)
    y = base - 28

    # Device table with header fill + zebra striping.
    def table(title, cols, data, col_x, row_color=None):
        nonlocal y
        if y < 120:
            c.new_page()
            y = PAGE_H - margin
        c.text(margin, y, title, size=12, font="bold")
        y -= 16
        c.rect(margin, y - 2, PAGE_W - 2 * margin, 16, fill=_DARK)
        for cx, name in zip(col_x, cols):
            c.text(margin + cx, y + 2, name, size=8, font="bold", color=_WHITE)
        y -= 16
        for ri, row in enumerate(data):
            if y < 60:
                c.new_page()
                y = PAGE_H - margin
            if ri % 2 == 0:
                c.rect(margin, y - 2, PAGE_W - 2 * margin, 14, fill=_LIGHT)
            color = row_color(row) if row_color else (0.1, 0.12, 0.14)
            for cx, cell in zip(col_x, row):
                c.text(margin + cx, y + 1, str(cell)[:40], size=8, color=color)
            y -= 14
        y -= 14

    table("Devices", ["Device", "Driver", "Last (d)", "Fails", "Rehearse",
                      "Baseline"],
          rows, [0, 190, 300, 360, 410, 470])

    def sev_color(row):
        return {"critical": _FAIL, "high": _FAIL, "medium": _WARN}.get(
            row[1], _MUTED)
    table("Policy findings", ["Device", "Severity", "Rule", "Description"],
          prows or [["(none)", "", "", ""]],
          [0, 190, 260, 360], row_color=sev_color)

    # Footer page numbers.
    total_pages = len(c._pages)
    for i in range(total_pages):
        c._pages_index = i
        # draw footer directly on each page's op list
        ops = c._pages[i]
        ops.append("0.6 0.66 0.72 rg")
        ops.append(
            f"BT /F1 8 Tf {PAGE_W - 90:.0f} 24 Td "
            f"(Page {i + 1} of {total_pages}) Tj ET")
        ops.append(f"BT /F1 8 Tf {margin} 24 Td (otitbup) Tj ET")
    return c.render()


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
