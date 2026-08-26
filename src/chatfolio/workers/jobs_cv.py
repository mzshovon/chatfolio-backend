import json
import uuid
from typing import Any

import structlog

from chatfolio.config.settings import LLMTask
from chatfolio.cv_parsing import CVParsingError, extract_text
from chatfolio.llm.factory import LLMProviderFactory
from chatfolio.llm.prompts.cv_extraction import CV_EXTRACTION_SYSTEM_PROMPT
from chatfolio.models.cv import CVStatus, UploadedCV
from chatfolio.models.profile import CandidateProfile
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
        except CVParsingError as exc:
            cv.status = CVStatus.FAILED
            cv.error_message = str(exc)
        except Exception:
            logger.exception("parse_cv_job.failed", cv_id=cv_id)
            cv.status = CVStatus.FAILED
            cv.error_message = "CV parsing failed unexpectedly. Please retry."
        finally:
            await session.commit()
