import asyncio
from typing import Any

import structlog
from arq import cron
from arq.connections import RedisSettings as ArqRedisSettings

from chatfolio.billing.support_kori import SupportKoriLicenseStrategy
from chatfolio.config.settings import get_settings
from chatfolio.db.session import get_sessionmaker
from chatfolio.llm.factory import LLMProviderFactory
from chatfolio.notifications.sms_vendor import VendorSmsSender
from chatfolio.notifications.smtp_email import SmtpEmailSender
from chatfolio.services.embedding_service import reconcile_missing_embeddings
from chatfolio.storage.s3_storage import S3StorageBackend
from chatfolio.vectorstore.chroma_store import ChromaVectorStore
from chatfolio.vectorstore.local_embedder import embed_texts
from chatfolio.workers.jobs_billing import (
    expire_subscriptions_job,
    expiry_reminder_job,
    revalidate_licenses_job,
)
from chatfolio.workers.jobs_cv import parse_cv_job
from chatfolio.workers.jobs_embedding import embed_content_job
from chatfolio.workers.queue import get_arq_pool

settings = get_settings()
logger = structlog.get_logger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    ctx["settings"] = settings
    ctx["sessionmaker"] = get_sessionmaker()
    ctx["storage"] = S3StorageBackend(settings.storage)
    ctx["llm_factory"] = LLMProviderFactory(settings.llm)
    ctx["license_strategy"] = SupportKoriLicenseStrategy(settings.support_kori)
    ctx["email_sender"] = SmtpEmailSender(settings.email)
    ctx["sms_sender"] = VendorSmsSender(settings.sms)
    ctx["vector_store"] = ChromaVectorStore(settings.vectorstore)
    ctx["embed_texts"] = embed_texts

    # Pre-warm the local embedding model serially, before any job can run: embed_texts()
    # downloads an ~80MB ONNX model lazily on its *first* call with no locking around that
    # check-and-fetch. arq runs jobs concurrently, so a burst of embed_content_job calls that
    # all hit "first call" at once (e.g. right after scripts/backfill_embeddings.py) each spawn
    # their own thread independently racing to download the same file — observed directly as
    # a dozen simultaneous progress bars fighting over the same bandwidth. One warm-up call
    # here means every real job call afterward just hits the on-disk cache.
    await asyncio.to_thread(embed_texts, ["warmup"])

    # Self-healing check (2026-08-22): re-enqueue any embedding whose Chroma vector has gone
    # missing while its Postgres pointer row is still there — see reconcile_missing_embeddings's
    # docstring for why this is needed even after fixing the root cause that first triggered it.
    pool = await get_arq_pool()
    reconciled = await reconcile_missing_embeddings(ctx["sessionmaker"], ctx["vector_store"], pool)
    if reconciled:
        logger.warning("worker.startup_reconciled_embeddings", count=reconciled)


async def shutdown(ctx: dict[str, Any]) -> None:
    pass


class WorkerSettings:
    functions: list[Any] = [
        parse_cv_job,
        embed_content_job,
        expire_subscriptions_job,
        revalidate_licenses_job,
        expiry_reminder_job,
    ]
    # UTC. Housekeeping hourly; refund checks and reminders once a day, off-peak.
    cron_jobs = [
        cron(expire_subscriptions_job, minute={5}),
        cron(revalidate_licenses_job, hour={3}, minute={30}),
        cron(expiry_reminder_job, hour={9}, minute={0}),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = ArqRedisSettings(
        host=settings.redis.host, port=settings.redis.port, database=settings.redis.db
    )
