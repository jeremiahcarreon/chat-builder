"""Extracts text from uploaded documents, keeping page numbers where the format has them."""

import html
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
log = logging.getLogger(__name__)

DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".markdown", ".html", ".htm"} | IMAGE_EXTENSIONS

# A PDF page with less extractable text than this is treated as a scan and OCR'd.
MIN_PAGE_CHARS = 25
# OCR is slow on CPU (a few seconds a page), so very long scans are capped.
MAX_OCR_PAGES = 500


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
        texts = [_clean(page.extract_text() or "") for page in reader.pages]
    except (PdfReadError, ValueError, KeyError) as exc:
        raise ParseError(f"Could not read PDF: {exc}") from exc

    # Pages without a usable text layer are scans: read them with OCR.
    scanned = [i for i, text in enumerate(texts) if len(text) < MIN_PAGE_CHARS]
    if scanned:
        from app.rag.ocr import OCRUnavailable, ocr_pdf_page

        if len(scanned) > MAX_OCR_PAGES:
            raise ParseError(f"This PDF has {len(scanned)} scanned pages; the limit for OCR is {MAX_OCR_PAGES}.")
        try:
            for i in scanned:
                recognized = _clean(ocr_pdf_page(path, i))
                if len(recognized) > len(texts[i]):
                    texts[i] = recognized
        except OCRUnavailable as exc:
            log.warning("Skipping OCR: %s", exc)
    sections = [Section(text, page=index + 1) for index, text in enumerate(texts) if text]
    if not sections:
        raise ParseError("No text found in this PDF, even with OCR. The scan may be blank or unreadable.")
    return sections


def _image(path: Path) -> list[Section]:
    from PIL import Image, UnidentifiedImageError

    from app.rag.ocr import OCRUnavailable, ocr_image

    try:
        with Image.open(path) as image:
            frames = []
            # Multi-page TIFFs hold one scanned page per frame.
            for index in range(getattr(image, "n_frames", 1)):
                image.seek(index)
                frames.append(Section(_clean(ocr_image(image.copy())), page=index + 1 if index else None))
    except UnidentifiedImageError as exc:
        raise ParseError("Could not read this image file.") from exc
    except OCRUnavailable as exc:
        raise ParseError("Text recognition (OCR) is not available on this server.") from exc
    if len(frames) > 1 and frames[0].page is None:
        frames[0].page = 1
    return frames


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
    elif suffix in IMAGE_EXTENSIONS:
        sections = _image(path)
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
