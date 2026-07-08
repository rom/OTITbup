"""A tiny vector PDF canvas (stdlib only).

Enough to draw a richly formatted report: filled/stroked rectangles, lines,
and text in Helvetica / Helvetica-Bold / Courier with colour. Coordinates
are PDF points with the origin at the bottom-left; A4 is 595 x 842. No
external library — the report is a plain PDF anyone can open, print, sign
and archive.
"""
from __future__ import annotations

import io

PAGE_W = 595
PAGE_H = 842
_FONTS = {"helv": "F1", "bold": "F2", "mono": "F3"}
# Approximate average glyph width (fraction of font size) for centring.
_AVG_W = {"F1": 0.50, "F2": 0.53, "F3": 0.60}


def _esc(text: str) -> str:
    return (text.replace("\\", r"\\").replace("(", r"\(")
            .replace(")", r"\)"))


class PDFCanvas:
    def __init__(self):
        self._pages: list[list[str]] = []
        self.new_page()

    def new_page(self) -> None:
        self._pages.append([])

    @property
    def _ops(self) -> list[str]:
        return self._pages[-1]

    # -------------------------------------------------------- primitives

    def fill(self, r: float, g: float, b: float) -> None:
        self._ops.append(f"{r:.3f} {g:.3f} {b:.3f} rg")

    def stroke(self, r: float, g: float, b: float) -> None:
        self._ops.append(f"{r:.3f} {g:.3f} {b:.3f} RG")

    def rect(self, x, y, w, h, fill=None, stroke=None, line_w=1.0) -> None:
        if fill is not None:
            self.fill(*fill)
        if stroke is not None:
            self.stroke(*stroke)
            self._ops.append(f"{line_w:.2f} w")
        op = "f" if fill is not None and stroke is None else (
            "B" if fill is not None and stroke is not None else "S")
        self._ops.append(f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re {op}")

    def line(self, x1, y1, x2, y2, color=(0, 0, 0), line_w=1.0) -> None:
        self.stroke(*color)
        self._ops.append(
            f"{line_w:.2f} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")

    def text(self, x, y, s, size=10, font="helv", color=(0, 0, 0)) -> None:
        f = _FONTS.get(font, "F1")
        self.fill(*color)
        self._ops.append(
            f"BT /{f} {size:.1f} Tf {x:.2f} {y:.2f} Td ({_esc(str(s))}) Tj ET")

    def text_centered(self, cx, y, s, size=10, font="helv",
                      color=(0, 0, 0)) -> None:
        f = _FONTS.get(font, "F1")
        width = len(str(s)) * size * _AVG_W.get(f, 0.5)
        self.text(cx - width / 2, y, s, size, font, color)

    # ------------------------------------------------------------- render

    def render(self) -> bytes:
        objects: dict[int, bytes] = {}
        catalog, pages_obj = 1, 2
        # Fonts as objects 3,4,5.
        objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
        objects[4] = (b"<< /Type /Font /Subtype /Type1 /BaseFont "
                      b"/Helvetica-Bold >>")
        objects[5] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>"
        next_num = 6
        page_nums = []
        for ops in self._pages:
            content = ("\n".join(ops)).encode("latin-1", "replace")
            c_num = next_num
            next_num += 1
            p_num = next_num
            next_num += 1
            page_nums.append(p_num)
            objects[c_num] = (
                b"<< /Length " + str(len(content)).encode()
                + b" >>\nstream\n" + content + b"\nendstream")
            objects[p_num] = (
                b"<< /Type /Page /Parent " + str(pages_obj).encode()
                + b" 0 R /MediaBox [0 0 " + f"{PAGE_W} {PAGE_H}".encode()
                + b"] /Contents " + str(c_num).encode()
                + b" 0 R /Resources << /Font << /F1 3 0 R /F2 4 0 R "
                b"/F3 5 0 R >> >> >>")
        kids = b" ".join(str(n).encode() + b" 0 R" for n in page_nums)
        objects[catalog] = (b"<< /Type /Catalog /Pages "
                            + str(pages_obj).encode() + b" 0 R >>")
        objects[pages_obj] = (
            b"<< /Type /Pages /Kids [" + kids + b"] /Count "
            + str(len(page_nums)).encode() + b" >>")

        out = io.BytesIO()
        out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = {}
        for num in sorted(objects):
            offsets[num] = out.tell()
            out.write(str(num).encode() + b" 0 obj\n" + objects[num]
                      + b"\nendobj\n")
        xref_pos = out.tell()
        count = len(objects) + 1
        out.write(b"xref\n0 " + str(count).encode() + b"\n")
        out.write(b"0000000000 65535 f \n")
        for num in range(1, count):
            out.write(f"{offsets[num]:010d} 00000 n \n".encode())
        out.write(b"trailer\n<< /Size " + str(count).encode()
                  + b" /Root 1 0 R >>\nstartxref\n"
                  + str(xref_pos).encode() + b"\n%%EOF")
        return out.getvalue()
