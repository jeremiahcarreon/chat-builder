import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Chunk, DataSource

TOP_K = 6
# Cosine distance above which a chunk is considered unrelated to the question.
MAX_DISTANCE = 0.75


@dataclass
class Excerpt:
    source_name: str
    page: int | None
    text: str
    distance: float

    @property
    def label(self) -> str:
        return f"{self.source_name}, page {self.page}" if self.page else self.source_name


def search_chunks(app_id: uuid.UUID, query_vector: list[float], k: int = TOP_K) -> list[Excerpt]:
    with SessionLocal() as db:
        distance = Chunk.embedding.cosine_distance(query_vector)
        rows = db.execute(
            select(Chunk.content, Chunk.meta, DataSource.name, distance.label("distance"))
            .join(DataSource, DataSource.id == Chunk.source_id)
            .where(Chunk.app_id == app_id, DataSource.status == "ready")
            .order_by(distance)
            .limit(k)
        ).all()
    return [
        Excerpt(name, meta.get("page"), content, float(dist))
        for content, meta, name, dist in rows
        if dist <= MAX_DISTANCE
    ]
