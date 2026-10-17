import json
import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.config.settings import LLMTask
from chatfolio.core.redis import get_redis
from chatfolio.cv_parsing import CVParsingError, extract_text
from chatfolio.entitlements.cache import EntitlementCache
from chatfolio.entitlements.catalog import FeatureKey
from chatfolio.llm.factory import LLMProviderFactory
from chatfolio.llm.prompts.cv_extraction import CV_EXTRACTION_SYSTEM_PROMPT
from chatfolio.models.cv import CVStatus, UploadedCV
from chatfolio.models.profile import CandidateProfile
from chatfolio.services.entitlement_guard import EntitlementGuard
from chatfolio.services.entitlement_service import EntitlementService
from chatfolio.services.quota_service import QuotaService
from chatfolio.storage.base import StorageBackend

logger = structlog.get_logger(__name__)


async def parse_cv_job(ctx: dict[str, Any], cv_id: str) -> None:
    """Extracts text from an uploaded CV and structures it via the configured LLM provider.

    Resources (sessionmaker/storage/llm_factory) come from ctx, populated in
    workers/worker.py's on_startup — this keeps the job a plain function tests can call
    directly with a hand-built ctx, without spinning up a real arq worker.
    """
    sessionmaker = ctx["sessionmaker"]
    storage: StorageBackend = ctx["storage"]
    llm_factory: LLMProviderFactory = ctx["llm_factory"]

    async with sessionmaker() as session:
        cv = await session.get(UploadedCV, uuid.UUID(cv_id))
        if cv is None:
            logger.warning("parse_cv_job.cv_not_found", cv_id=cv_id)
            return

        cv.status = CVStatus.PROCESSING
        await session.commit()

        try:
            content = await storage.download(cv.file_key)
            raw_text = extract_text(cv.file_type, content)

            provider = llm_factory.for_task(LLMTask.EXTRACTION)
            completion = await provider.complete(
                system=CV_EXTRACTION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": raw_text}],
                json_mode=True,
            )

            cv.raw_text = raw_text
            cv.parsed_json = json.loads(completion.content)
            cv.status = CVStatus.PARSED
            cv.error_message = None

            profile = await session.get(CandidateProfile, cv.profile_id)
            if profile is not None:
                profile.ai_tokens_used += completion.tokens_used
                await _tally_tokens(session, profile.user_id, completion.tokens_used)
        except CVParsingError as exc:
            cv.status = CVStatus.FAILED
            cv.error_message = str(exc)
        except Exception:
            logger.exception("parse_cv_job.failed", cv_id=cv_id)
            cv.status = CVStatus.FAILED
            cv.error_message = "CV parsing failed unexpectedly. Please retry."
        finally:
            await session.commit()


async def _tally_tokens(session: AsyncSession, user_id: uuid.UUID, tokens: int) -> None:
    """Counts CV-extraction tokens against the owner's AI budget. Tally only — it never blocks a
    parse — and a failure here must not turn a successfully parsed CV into a FAILED one."""
    try:
        redis = get_redis()
        cache = EntitlementCache(redis)
        guard = EntitlementGuard(
            EntitlementService(session, cache), QuotaService(session, redis)
        )
        await guard.add_usage(user_id, FeatureKey.AI_TOKENS_LIMIT, tokens)
    except Exception:
        logger.warning("parse_cv_job.token_tally_failed", exc_info=True)
