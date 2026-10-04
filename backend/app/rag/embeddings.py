"""Embeddings from EmbeddingGemma running in Ollama.

EmbeddingGemma is trained with task prefixes: documents and search queries are
embedded differently, which noticeably improves retrieval.
"""

import httpx

from app.config import get_settings
from app.models import EMBEDDING_DIM

BATCH_SIZE = 32


class EmbeddingError(Exception):
    pass


def document_prompt(text: str, title: str | None = None) -> str:
    return f"title: {title or 'none'} | text: {text}"


def query_prompt(text: str) -> str:
    return f"task: search result | query: {text}"


async def embed(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    vectors: list[list[float]] = []
    async with httpx.AsyncClient(timeout=300) as client:
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start : start + BATCH_SIZE]
            try:
                response = await client.post(
                    f"{settings.ollama_url}/api/embed",
                    json={"model": settings.embedding_model, "input": batch},
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise EmbeddingError(f"Embedding service unavailable: {exc.__class__.__name__}") from exc
            result = response.json()["embeddings"]
            if any(len(v) != EMBEDDING_DIM for v in result):
                raise EmbeddingError(
                    f"Embedding model returned vectors of the wrong size (expected {EMBEDDING_DIM})"
                )
            vectors.extend(result)
    return vectors


async def embed_documents(texts: list[str], title: str | None = None) -> list[list[float]]:
    return await embed([document_prompt(t, title) for t in texts])


async def embed_query(text: str) -> list[float]:
    return (await embed([query_prompt(text)]))[0]
