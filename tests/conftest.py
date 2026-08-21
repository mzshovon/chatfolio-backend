import os

# Force tests onto dedicated database + storage bucket *before* anything imports
# chatfolio.config.settings (e.g. `from chatfolio.main import app` in a test module creates
# the app eagerly at import time). Tests must never touch the resources a developer's running
# `api`/`worker` containers use — the cleanup fixture below truncates every DB table after each
# test, and uploaded test files would otherwise accumulate forever in the dev bucket.
os.environ["DATABASE_NAME"] = os.environ.get("TEST_DATABASE_NAME", "chatfolio_test")
os.environ["STORAGE_BUCKET"] = os.environ.get("TEST_STORAGE_BUCKET", "chatfolio-cv-test")
os.environ["REDIS_DB"] = os.environ.get("TEST_REDIS_DB", "1")

from collections.abc import AsyncGenerator  # noqa: E402

import aioboto3  # noqa: E402
import pytest  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402
from sqlalchemy import text  # noqa: E402

import chatfolio.models  # noqa: F401,E402 (registers all model modules on Base.metadata)
from chatfolio.api.deps import get_embed_texts, get_vector_store  # noqa: E402
from chatfolio.config.settings import get_settings  # noqa: E402
from chatfolio.db.base import Base  # noqa: E402
from chatfolio.db.session import get_engine  # noqa: E402
from chatfolio.main import app  # noqa: E402
from chatfolio.workers.queue import get_arq_pool  # noqa: E402
from tests.factories.fake_vectorstore import FakeVectorStore  # noqa: E402

# No test needs a real Chroma connection or the local embedding model: enqueue-only paths
# (create/update on Experience/Project/Skill/Education, and PortfolioSection approve) only
# push a job onto the queue and never execute it here (no worker runs during tests, same as
# CV parsing — see test_jobs_cv.py). The only *synchronous* vector_store call in the request
# path is EmbeddingService.delete_embed() and RAGService.retrieve() (public chat), so a fake is
# enough for the whole suite; embed_content_job itself is tested directly with a hand-built
# ctx, same pattern as parse_cv_job. The real embedder also downloads an ~80MB ONNX model on
# first use — never acceptable in a test suite regardless of the Chroma question.
app.dependency_overrides[get_vector_store] = lambda: FakeVectorStore()
app.dependency_overrides[get_embed_texts] = lambda: lambda texts: [[0.1, 0.2, 0.3] for _ in texts]


@pytest.fixture(scope="session", autouse=True)
async def _prepare_test_database() -> AsyncGenerator[None]:
    settings = get_settings()
    assert "test" in settings.database.name, (
        f"Refusing to run tests against non-test database {settings.database.name!r}. "
        "Set TEST_DATABASE_NAME or fix DATABASE_NAME."
    )

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_database() -> AsyncGenerator[None]:
    yield
    settings = get_settings()
    assert "test" in settings.database.name  # same safety net as above, cheap to repeat

    engine = get_engine()
    table_names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE TABLE {table_names} CASCADE"))


async def _empty_test_bucket() -> None:
    settings = get_settings().storage
    assert "test" in settings.bucket, (
        f"Refusing to touch non-test storage bucket {settings.bucket!r}. "
        "Set TEST_STORAGE_BUCKET or fix STORAGE_BUCKET."
    )

    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=settings.endpoint_url,
        region_name=settings.region,
        aws_access_key_id=settings.access_key.get_secret_value(),
        aws_secret_access_key=settings.secret_key.get_secret_value(),
    ) as client:
        try:
            await client.head_bucket(Bucket=settings.bucket)
        except ClientError:
            await client.create_bucket(Bucket=settings.bucket)
            return

        paginator = client.get_paginator("list_objects_v2")
        async for page in paginator.paginate(Bucket=settings.bucket):
            keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if keys:
                await client.delete_objects(Bucket=settings.bucket, Delete={"Objects": keys})


@pytest.fixture(scope="session", autouse=True)
async def _prepare_test_bucket() -> AsyncGenerator[None]:
    await _empty_test_bucket()
    yield
    await _empty_test_bucket()


async def _flush_test_redis_db() -> None:
    settings = get_settings().redis
    assert settings.db != 0, (
        f"Refusing to touch Redis db index {settings.db} (0 is the dev/worker default). "
        "Set TEST_REDIS_DB or fix REDIS_DB."
    )
    pool = await get_arq_pool()
    await pool.flushdb()


@pytest.fixture(autouse=True)
async def _prepare_test_queue() -> AsyncGenerator[None]:
    # Function-scoped, not session-scoped: slowapi's rate-limit counters live in this same
    # Redis db, keyed by client IP — every test's ASGITransport client shares one IP, so a
    # session-scoped flush would let rate-limit hits accumulate across unrelated tests and
    # cause spurious 429s far away from whichever test actually earned them.
    await _flush_test_redis_db()
    yield
    await _flush_test_redis_db()
