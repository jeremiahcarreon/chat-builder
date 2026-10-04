"""Extracts text from uploaded documents, keeping page numbers where the format has them."""

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".markdown", ".html", ".htm"}


class ParseError(Exception):
    pass


@dataclass
class Section:
    text: str
    page: int | None = None


def _clean(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _pdf(path: Path) -> list[Section]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            reader.decrypt("")
        sections = [
            Section(_clean(page.extract_text() or ""), page=index + 1)
            for index, page in enumerate(reader.pages)
        ]
    except (PdfReadError, ValueError, KeyError) as exc:
        raise ParseError(f"Could not read PDF: {exc}") from exc
    sections = [s for s in sections if s.text]
    if not sections:
        raise ParseError(
            "No text found in this PDF. It may be a scanned image; scanned PDFs need OCR, "
            "which isn't supported yet."
        )
    return sections


def _docx(path: Path) -> list[Section]:
    import docx
    from docx.opc.exceptions import PackageNotFoundError

    try:
        document = docx.Document(str(path))
    except (PackageNotFoundError, KeyError, ValueError) as exc:
        raise ParseError(f"Could not read Word document: {exc}") from exc
    parts = []
    for block in document.element.body.iterchildren():
        tag = block.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = "".join(node.text or "" for node in block.iter() if node.tag.endswith("}t"))
            if text.strip():
                parts.append(text)
        elif tag == "tbl":
            for row in block.iter():
                if row.tag.endswith("}tr"):
                    cells = []
                    for cell in row.iterchildren():
                        if cell.tag.endswith("}tc"):
                            cells.append(
                                " ".join(n.text or "" for n in cell.iter() if n.tag.endswith("}t")).strip()
                            )
                    parts.append(" | ".join(cells))
    return [Section(_clean("\n".join(parts)))]


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "head"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _html(path: Path) -> list[Section]:
    parser = _TextExtractor()
    parser.feed(path.read_text(errors="replace"))
    return [Section(_clean(html.unescape("".join(parser.parts))))]


def extract(path: Path) -> list[Section]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        sections = _pdf(path)
    elif suffix == ".docx":
        sections = _docx(path)
    elif suffix in (".html", ".htm"):
        sections = _html(path)
    elif suffix in (".txt", ".md", ".markdown"):
        sections = [Section(_clean(path.read_text(errors="replace")))]
    else:
        raise ParseError(f"Unsupported file type: {suffix}")
    sections = [s for s in sections if s.text]
    if not sections:
        raise ParseError("No text found in this file.")
    return sections
