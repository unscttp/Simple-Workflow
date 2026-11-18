"""Reading, writing and editing report documents (Markdown, DOCX, PDF)."""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document

READABLE_FORMATS = (".md", ".docx")
EDIT_MODES = ("append", "replace_section", "rewrite")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def content_lines(content: str) -> list[str]:
    cleaned = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    return cleaned.split("\n") if cleaned else []


# -- writing -----------------------------------------------------------------


def write_markdown(path: Path, title: str, content: str) -> None:
    path.write_text(f"# {title.strip()}\n\n{content.strip()}\n", encoding="utf-8")


def write_docx(path: Path, title: str, content: str) -> None:
    document = Document()
    document.add_heading(title.strip(), level=1)
    for line in content_lines(content):
        document.add_paragraph(line.strip())
    document.save(path)


def write_pdf(path: Path, title: str, content: str) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    # A CID font so Chinese text renders; the built-in Helvetica has no CJK glyphs.
    font = "STSong-Light"
    if font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(font))
    pdf = canvas.Canvas(str(path), pagesize=A4)
    _, height = A4
    y = height - 60
    pdf.setFont(font, 16)
    pdf.drawString(50, y, title.strip() or "Untitled report")
    y -= 30
    pdf.setFont(font, 11)
    for line in content_lines(content):
        if y < 50:
            pdf.showPage()
            pdf.setFont(font, 11)
            y = height - 60
        pdf.drawString(50, y, line.strip())
        y -= 18
    pdf.save()


WRITERS = {"md": write_markdown, "docx": write_docx, "pdf": write_pdf}


# -- reading -----------------------------------------------------------------


def read_text(path: Path) -> str:
    if path.suffix.lower() == ".md":
        return path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".docx":
        return "\n".join(paragraph.text for paragraph in Document(path).paragraphs)
    raise ValueError(f"Only {', '.join(READABLE_FORMATS)} files can be read.")


# -- editing -----------------------------------------------------------------


def _markdown_section(lines: list[str], section: str) -> tuple[int, int]:
    """Line range of a heading and its body, up to the next heading of the same level."""
    headings = []
    for index, line in enumerate(lines):
        match = _HEADING.match(line.strip())
        if match:
            headings.append((index, len(match.group(1)), match.group(2).strip()))
    wanted = section.strip().lstrip("#").strip().lower()
    for index, level, title in headings:
        if title.lower() == wanted:
            end = next((i for i, other, _ in headings if i > index and other <= level), len(lines))
            return index, end
    raise ValueError(f"Section not found: {section!r}")


def edit_markdown(original: str, mode: str, content: str, section: str | None) -> str:
    text = original.replace("\r\n", "\n").replace("\r", "\n")
    if mode == "append":
        merged = text.rstrip("\n")
        return f"{merged}\n\n{content.strip()}\n" if merged else f"{content.strip()}\n"
    if mode == "rewrite":
        return f"{content.strip()}\n"
    lines = text.split("\n")
    start, end = _markdown_section(lines, section or "")
    replaced = lines[:start] + [lines[start], *content.strip().splitlines()] + lines[end:]
    return "\n".join(replaced).rstrip("\n") + "\n"


def edit_docx(path: Path, mode: str, content: str, section: str | None) -> None:
    document = Document(path)
    new_lines = content.strip().splitlines()
    if mode == "append":
        for line in new_lines:
            document.add_paragraph(line)
    elif mode == "rewrite":
        for paragraph in list(document.paragraphs):
            paragraph._element.getparent().remove(paragraph._element)
        for line in new_lines:
            document.add_paragraph(line)
    else:
        paragraphs = document.paragraphs
        wanted = (section or "").strip().lower()
        start = next(
            (i for i, p in enumerate(paragraphs) if p.text.strip().lower() == wanted), None
        )
        if start is None:
            raise ValueError(f"Section not found: {section!r}")
        end = next(
            (
                i
                for i in range(start + 1, len(paragraphs))
                if str(paragraphs[i].style.name).lower().startswith("heading")
            ),
            len(paragraphs),
        )
        for paragraph in paragraphs[start + 1 : end]:
            paragraph._element.getparent().remove(paragraph._element)
        anchor = paragraphs[start]._element
        for line in new_lines:
            added = document.add_paragraph(line)
            anchor.addnext(added._element)
            anchor = added._element
    document.save(path)
