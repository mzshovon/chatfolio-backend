from sqlalchemy import JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from chatfolio.db.base import Base
from chatfolio.models.mixins import ProfileChildMixin, TimestampMixin


class VectorEmbedding(ProfileChildMixin, TimestampMixin, Base):
    """Postgres-side pointer to what's embedded in Chroma — lets a full reindex be driven from
    Postgres alone (source of truth for *what should be embedded*), and lets us look up/delete
    the Chroma vector for a given source row without round-tripping through Chroma to find it.
    """

    __tablename__ = "vector_embeddings"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", name="uq_vector_embedding_source"),
    )

    source_type: Mapped[str] = mapped_column(String(50))
    source_id: Mapped[str] = mapped_column(String(255))
    chroma_ref_id: Mapped[str] = mapped_column(String(255), unique=True)
    chunk_text: Mapped[str] = mapped_column(Text)
    chunk_metadata: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
