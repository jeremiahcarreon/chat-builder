"""Splits extracted text into overlapping chunks sized for embedding and retrieval.

About 1,800 characters (roughly 400-500 tokens) per chunk, split on paragraph
and sentence boundaries where possible, with some overlap so facts that span a
boundary still land together in at least one chunk.
"""

import re
from dataclasses import dataclass

from app.rag.parsing import Section

TARGET_CHARS = 1800
OVERLAP_CHARS = 400


@dataclass
class Chunk:
    text: str
    page: int | None


def _pieces(text: str) -> list[str]:
    """Paragraphs, with oversized paragraphs split into sentences (and hard-split if needed)."""
    pieces = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= TARGET_CHARS:
            pieces.append(paragraph)
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            while len(sentence) > TARGET_CHARS:
                pieces.append(sentence[:TARGET_CHARS])
                sentence = sentence[TARGET_CHARS - OVERLAP_CHARS :]
            if sentence:
                pieces.append(sentence)
    return pieces


def chunk_sections(sections: list[Section]) -> list[Chunk]:
    pieces = [(piece, section.page) for section in sections for piece in _pieces(section.text)]
    chunks: list[Chunk] = []
    current: list[tuple[str, int | None]] = []
    size = 0
    new_since_flush = 0

    for piece, page in pieces:
        if current and size + len(piece) > TARGET_CHARS:
            chunks.append(Chunk("\n\n".join(t for t, _ in current), current[0][1]))
            # Carry the tail of this chunk into the next one as overlap.
            overlap: list[tuple[str, int | None]] = []
            overlap_size = 0
            for item in reversed(current):
                if overlap_size + len(item[0]) > OVERLAP_CHARS:
                    break
                overlap.insert(0, item)
                overlap_size += len(item[0])
            current, size, new_since_flush = overlap, overlap_size, 0
        current.append((piece, page))
        size += len(piece)
        new_since_flush += 1

    if current and new_since_flush:
        chunks.append(Chunk("\n\n".join(t for t, _ in current), current[0][1]))
    return chunks
