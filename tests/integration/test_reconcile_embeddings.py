import uuid

from chatfolio.core.security import hash_password
from chatfolio.db.session import get_sessionmaker
from chatfolio.models.profile import CandidateProfile
from chatfolio.models.user import User, UserRole
from chatfolio.services.embedding_service import reconcile_missing_embeddings
from chatfolio.workers.jobs_embedding import embed_content_job
from tests.factories.fake_vectorstore import FakeVectorStore


class SpyJobQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, tuple[object, ...]]] = []

    async def enqueue_job(self, function: str, *args: object) -> None:
        self.enqueued.append((function, args))


def fake_embed_texts(texts: list[str]) -> list[list[float]]:
    return [[0.1, 0.2, 0.3] for _ in texts]


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


async def _seed_pointer(profile_id: uuid.UUID, source_type: str, chunk_text: str) -> str:
    source_id = uuid.uuid4()
    ctx = {
        "sessionmaker": get_sessionmaker(),
        "vector_store": FakeVectorStore(),
        "embed_texts": fake_embed_texts,
    }
    await embed_content_job(ctx, str(profile_id), source_type, str(source_id), chunk_text)
    return f"{source_type}:{source_id}"


async def test_reconcile_reenqueues_every_pointer_when_chroma_is_empty() -> None:
    profile = await _create_profile()
    ref_a = await _seed_pointer(profile.id, "experience", "Backend engineer at Acme.")
    ref_b = await _seed_pointer(profile.id, "skills", "Skill: PHP")

    # Simulates exactly the bug found on 2026-08-22: Postgres pointer rows survive, but the
    # Chroma vector store behind them has lost everything (wrong volume mount, wiped volume, etc).
    empty_store = FakeVectorStore(existing_ids=[])
    job_queue = SpyJobQueue()

    reconciled = await reconcile_missing_embeddings(get_sessionmaker(), empty_store, job_queue)

    assert reconciled == 2
    enqueued_refs = {f"{args[1]}:{args[2]}" for _, args in job_queue.enqueued}
    assert enqueued_refs == {ref_a, ref_b}


async def test_reconcile_skips_pointers_that_still_exist_in_chroma() -> None:
    profile = await _create_profile()
    ref_a = await _seed_pointer(profile.id, "experience", "Backend engineer at Acme.")
    ref_b = await _seed_pointer(profile.id, "skills", "Skill: PHP")

    partial_store = FakeVectorStore(existing_ids=[ref_a])
    job_queue = SpyJobQueue()

    reconciled = await reconcile_missing_embeddings(get_sessionmaker(), partial_store, job_queue)

    assert reconciled == 1
    assert len(job_queue.enqueued) == 1
    _, args = job_queue.enqueued[0]
    assert f"{args[1]}:{args[2]}" == ref_b


async def test_reconcile_is_a_noop_when_nothing_is_missing() -> None:
    profile = await _create_profile()
    ref_a = await _seed_pointer(profile.id, "experience", "Backend engineer at Acme.")

    full_store = FakeVectorStore(existing_ids=[ref_a])
    job_queue = SpyJobQueue()

    reconciled = await reconcile_missing_embeddings(get_sessionmaker(), full_store, job_queue)

    assert reconciled == 0
    assert job_queue.enqueued == []


async def test_reconcile_returns_zero_when_no_pointers_exist_at_all() -> None:
    job_queue = SpyJobQueue()
    reconciled = await reconcile_missing_embeddings(
        get_sessionmaker(), FakeVectorStore(), job_queue
    )
    assert reconciled == 0
    assert job_queue.enqueued == []
