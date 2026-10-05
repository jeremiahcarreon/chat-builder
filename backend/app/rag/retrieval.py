import uuid
from dataclasses import dataclass

from sqlalchemy import String, func, or_, select
from sqlalchemy.dialects.postgresql import array

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


# Documents tagged with a SKU the question names are searched first, with a looser bar.
TAGGED_K = 4
TAGGED_MAX_DISTANCE = 0.9


def search_chunks(
    app_id: uuid.UUID, query_vector: list[float], k: int = TOP_K, sku_keys: list[str] | None = None
) -> list[Excerpt]:
    """Closest passages for this app. When the question names SKUs, documents tagged
    with those SKUs come first, and documents tagged only with other SKUs are left out."""
    distance = Chunk.embedding.cosine_distance(query_vector)
    tags = DataSource.config["sku_keys"]
    untagged = or_(tags.is_(None), func.jsonb_array_length(tags) == 0)
    base = (
        select(Chunk.content, Chunk.meta, DataSource.name, distance.label("distance"))
        .join(DataSource, DataSource.id == Chunk.source_id)
        .where(Chunk.app_id == app_id, DataSource.status == "ready")
        .order_by(distance)
    )
    with SessionLocal() as db:
        if sku_keys:
            matching = tags.has_any(array(sku_keys, type_=String))
            tagged = db.execute(base.where(matching).limit(TAGGED_K)).all()
            general = db.execute(base.where(untagged).limit(k)).all()
            rows = [(r, TAGGED_MAX_DISTANCE) for r in tagged] + [(r, MAX_DISTANCE) for r in general]
        else:
            rows = [(r, MAX_DISTANCE) for r in db.execute(base.limit(k)).all()]
    excerpts, seen = [], set()
    for (content, meta, name, dist), limit in rows:
        if dist <= limit and content not in seen:
            seen.add(content)
            excerpts.append(Excerpt(name, meta.get("page"), content, float(dist)))
    return excerpts[: max(k, TAGGED_K)]
