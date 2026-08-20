import uuid
from typing import Any

from sqlalchemy import select

from chatfolio.core.security import hash_password
from chatfolio.db.session import get_sessionmaker
from chatfolio.models.embedding import VectorEmbedding
from chatfolio.models.profile import CandidateProfile
from chatfolio.models.user import User, UserRole
from chatfolio.workers.jobs_embedding import embed_content_job
from tests.factories.fake_vectorstore import FakeVectorStore


def fake_embed_texts(texts: list[str]) -> list[list[float]]:
    return [[0.1, 0.2, 0.3] for _ in texts]


def broken_embed_texts(texts: list[str]) -> list[list[float]]:
    raise RuntimeError("embedding backend unavailable")


async def _create_profile() -> CandidateProfile:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        user = User(
            email=f"{uuid.uuid4()}@example.com",
            hashed_password=hash_password("supersecret123"),
            role=UserRole.CANDIDATE,
        )
        session.add(user)
        await session.flush()

        profile = CandidateProfile(user_id=user.id)
        session.add(profile)
        await session.commit()
        await session.refresh(profile)
        return profile


def _ctx(vector_store: FakeVectorStore, embed_texts: Any = fake_embed_texts) -> dict[str, Any]:
    return {
        "sessionmaker": get_sessionmaker(),
        "vector_store": vector_store,
        "embed_texts": embed_texts,
    }


async def _get_pointer_row(chroma_ref_id: str) -> VectorEmbedding | None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(
            select(VectorEmbedding).where(VectorEmbedding.chroma_ref_id == chroma_ref_id)
        )
        return result.scalar_one_or_none()


async def test_embed_content_job_creates_pointer_row_and_upserts_vector() -> None:
    profile = await _create_profile()
    source_id = uuid.uuid4()
    vector_store = FakeVectorStore()

    chunk_text = "Backend engineer at Acme."
    await embed_content_job(
        _ctx(vector_store), str(profile.id), "experience", str(source_id), chunk_text
    )

    chroma_ref_id = f"experience:{source_id}"
    row = await _get_pointer_row(chroma_ref_id)
    assert row is not None
    assert row.chunk_text == "Backend engineer at Acme."
    assert row.profile_id == profile.id

    assert len(vector_store.upserted) == 1
    upsert = vector_store.upserted[0]
    assert upsert["ids"] == [chroma_ref_id]
    assert upsert["documents"] == ["Backend engineer at Acme."]
    assert upsert["metadatas"] == [
        {"profile_id": str(profile.id), "source_type": "experience", "source_id": str(source_id)}
    ]


async def test_embed_content_job_updates_existing_pointer_row_on_re_embed() -> None:
    profile = await _create_profile()
    source_id = uuid.uuid4()
    vector_store = FakeVectorStore()

    await embed_content_job(
        _ctx(vector_store), str(profile.id), "experience", str(source_id), "v1 text"
    )
    await embed_content_job(
        _ctx(vector_store), str(profile.id), "experience", str(source_id), "v2 text"
    )

    chroma_ref_id = f"experience:{source_id}"
    row = await _get_pointer_row(chroma_ref_id)
    assert row is not None
    assert row.chunk_text == "v2 text"
    assert len(vector_store.upserted) == 2


async def test_embed_content_job_handles_embedding_failure_gracefully() -> None:
    profile = await _create_profile()
    source_id = uuid.uuid4()
    vector_store = FakeVectorStore()

    await embed_content_job(
        _ctx(vector_store, broken_embed_texts),
        str(profile.id),
        "experience",
        str(source_id),
        "text",
    )

    row = await _get_pointer_row(f"experience:{source_id}")
    assert row is None
    assert vector_store.upserted == []
