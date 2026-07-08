"""A tiny, dependency-free Markdown-to-HTML renderer.

Just enough to render the project's own docs (USAGE.md, FAQ.md, …) inside
the web UI: headings, fenced and inline code, bold/italic, links, unordered
and ordered lists, blockquotes, horizontal rules, tables (GitHub pipe
syntax), and paragraphs. Everything is HTML-escaped first, so rendering an
untrusted document cannot inject markup — we build only a fixed set of tags.

This is deliberately NOT a full CommonMark implementation; it targets the
subset our docs actually use. Unhandled syntax degrades to plain text.
"""
from __future__ import annotations

import html
import re

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_ULITEM = re.compile(r"^[-*]\s+(.*)$")
_OLITEM = re.compile(r"^(\d+)\.\s+(.*)$")
_HR = re.compile(r"^([-*_])\1{2,}\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")


def _inline(text: str) -> str:
    """Render inline spans on already-escaped text."""
    # Inline code first so its contents aren't further transformed. The text
    # is already escaped, so backticks wrap escaped content verbatim.
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # Links [label](url) — allow only http(s)/relative, never javascript:.
    def _link(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        if not re.match(r"^(https?:|/|#|\.|[\w./-]+$)", url):
            return label
        return f'<a href="{url}">{label}</a>'
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _link, text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    return text


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def render(md: str) -> str:
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    list_stack: list[str] = []   # open list tags, "ul"/"ol"

    def close_lists() -> None:
        while list_stack:
            out.append(f"</{list_stack.pop()}>")

    while i < n:
        line = lines[i]

        # Fenced code block.
        if line.lstrip().startswith("```"):
            close_lists()
            i += 1
            code: list[str] = []
            while i < n and not lines[i].lstrip().startswith("```"):
                code.append(html.escape(lines[i]))
                i += 1
            i += 1  # skip closing fence
            out.append("<pre><code>" + "\n".join(code) + "</code></pre>")
            continue

        stripped = line.strip()

        if not stripped:
            close_lists()
            i += 1
            continue

        if _HR.match(stripped):
            close_lists()
            out.append("<hr>")
            i += 1
            continue

        m = _HEADING.match(stripped)
        if m:
            close_lists()
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(html.escape(m.group(2)))}</h{level}>")
            i += 1
            continue

        # Table: a header row followed by a |---|---| separator.
        if "|" in line and i + 1 < n and _TABLE_SEP.match(lines[i + 1]):
            close_lists()
            header = _cells(line)
            out.append("<table><thead><tr>"
                       + "".join(f"<th>{_inline(html.escape(c))}</th>"
                                 for c in header)
                       + "</tr></thead><tbody>")
            i += 2
            while i < n and "|" in lines[i] and lines[i].strip():
                row = _cells(lines[i])
                out.append("<tr>" + "".join(
                    f"<td>{_inline(html.escape(c))}</td>" for c in row)
                    + "</tr>")
                i += 1
            out.append("</tbody></table>")
            continue

        if stripped.startswith(">"):
            close_lists()
            quote = _inline(html.escape(stripped.lstrip("> ").rstrip()))
            out.append(f"<blockquote>{quote}</blockquote>")
            i += 1
            continue

        m = _ULITEM.match(stripped)
        if m:
            if not list_stack or list_stack[-1] != "ul":
                close_lists()
                list_stack.append("ul")
                out.append("<ul>")
            out.append(f"<li>{_inline(html.escape(m.group(1)))}</li>")
            i += 1
            continue

        m = _OLITEM.match(stripped)
        if m:
            if not list_stack or list_stack[-1] != "ol":
                close_lists()
                list_stack.append("ol")
                out.append("<ol>")
            out.append(f"<li>{_inline(html.escape(m.group(2)))}</li>")
            i += 1
            continue

        # Paragraph: gather consecutive plain lines.
        close_lists()
        para = [stripped]
        i += 1
        while i < n and lines[i].strip() and not (
            _HEADING.match(lines[i].strip())
            or lines[i].lstrip().startswith(("```", ">", "- ", "* "))
            or _OLITEM.match(lines[i].strip())
            or _HR.match(lines[i].strip())
        ):
            para.append(lines[i].strip())
            i += 1
        out.append("<p>" + _inline(html.escape(" ".join(para))) + "</p>")

    close_lists()
    return "\n".join(out)
