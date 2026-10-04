"""Optical character recognition for scanned PDF pages and images.

Uses RapidOCR (PaddleOCR models on ONNX Runtime): CPU-only, no system packages,
models bundled with the Python package.
"""

import logging
import threading

import numpy as np

log = logging.getLogger(__name__)

# Pages are rendered at this resolution before OCR; 200 DPI is a good speed/accuracy balance.
RENDER_DPI = 200
# Recognized lines below this confidence are dropped as noise.
MIN_LINE_SCORE = 0.5

_engine = None
_lock = threading.Lock()


class OCRUnavailable(Exception):
    pass


def _get_engine():
    global _engine
    with _lock:
        if _engine is None:
            try:
                from rapidocr import RapidOCR
            except ImportError as exc:  # pragma: no cover - depends on installed extras
                raise OCRUnavailable("OCR is not installed") from exc
            logging.getLogger("RapidOCR").setLevel(logging.WARNING)
            _engine = RapidOCR()
        return _engine


def _lines_in_reading_order(boxes, texts, scores) -> list[str]:
    """Groups recognized fragments into lines, top to bottom, then left to right."""
    items = []
    for box, text, score in zip(boxes, texts, scores, strict=False):
        if score is not None and score < MIN_LINE_SCORE or not str(text).strip():
            continue
        ys = [point[1] for point in box]
        xs = [point[0] for point in box]
        items.append((min(ys), max(ys), min(xs), str(text).strip()))
    items.sort(key=lambda item: (item[0], item[2]))
    lines: list[list[tuple]] = []
    for item in items:
        top, bottom = item[0], item[1]
        if lines:
            last_top = min(i[0] for i in lines[-1])
            last_bottom = max(i[1] for i in lines[-1])
            # Same line if the vertical spans overlap by more than half.
            overlap = min(bottom, last_bottom) - max(top, last_top)
            if overlap > 0.5 * min(bottom - top, last_bottom - last_top):
                lines[-1].append(item)
                continue
        lines.append([item])
    return [" ".join(i[3] for i in sorted(line, key=lambda i: i[2])) for line in lines]


def ocr_image(image) -> str:
    """Text from a PIL image."""
    engine = _get_engine()
    result = engine(np.asarray(image.convert("RGB")))
    if result is None or not getattr(result, "txts", None):
        return ""
    return "\n".join(_lines_in_reading_order(result.boxes, result.txts, result.scores))


def ocr_pdf_page(path, page_index: int) -> str:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        image = pdf[page_index].render(scale=RENDER_DPI / 72).to_pil()
    finally:
        pdf.close()
    return ocr_image(image)
