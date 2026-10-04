"""OCR on generated scans. Uses the bundled RapidOCR models (no network)."""

import pytest
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.rag.ocr import _lines_in_reading_order
from app.rag.parsing import extract

pytest.importorskip("rapidocr")


def _scan(lines: list[str]) -> Image.Image:
    image = Image.new("RGB", (1200, 120 + 60 * len(lines)), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=32)
    for i, line in enumerate(lines):
        draw.text((40, 50 + 60 * i), line, fill="black", font=font)
    # Slight blur and tilt, like a real scan.
    return image.rotate(0.6, fillcolor="white", expand=True).filter(ImageFilter.GaussianBlur(0.5))


def test_scanned_pdf_is_read_with_ocr(tmp_path):
    pages = [_scan(["Error code E7 means the defrost sensor failed."]), _scan(["Replace part DS-4410."])]
    path = tmp_path / "scan.pdf"
    pages[0].save(path, save_all=True, append_images=pages[1:], resolution=150)
    sections = extract(path)
    assert [s.page for s in sections] == [1, 2]
    assert "defrost sensor" in sections[0].text
    assert "DS-4410" in sections[1].text


def test_image_is_read_with_ocr(tmp_path):
    path = tmp_path / "card.png"
    _scan(["Compressor warranty is 10 years."]).save(path)
    assert "10 years" in extract(path)[0].text


def test_fragments_are_ordered_into_lines():
    def box(x, y):
        return [[x, y], [x + 50, y], [x + 50, y + 20], [x, y + 20]]

    # Second line listed first, and the first line split into two fragments out of order.
    boxes = [box(0, 100), box(200, 2), box(0, 0)]
    texts = ["second line", "world", "hello"]
    assert _lines_in_reading_order(boxes, texts, [0.9, 0.9, 0.9]) == ["hello world", "second line"]
