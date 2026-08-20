import json
import uuid
from typing import Any

from chatfolio.core.security import hash_password
from chatfolio.db.session import get_sessionmaker
from chatfolio.models.cv import CVStatus, UploadedCV
from chatfolio.models.profile import CandidateProfile
from chatfolio.models.user import User, UserRole
from chatfolio.workers.jobs_cv import parse_cv_job
from tests.factories.documents import make_pdf_bytes
from tests.factories.fake_llm import FakeLLMFactory


class FakeStorage:
    def __init__(self, content: bytes) -> None:
        self._content = content

    async def upload(self, *, key: str, content: bytes, content_type: str) -> None:
        raise NotImplementedError

    async def download(self, key: str) -> bytes:
        return self._content

    async def generate_download_url(self, key: str, expires_in: int = 3600) -> str:
        raise NotImplementedError

    async def delete(self, key: str) -> None:
        raise NotImplementedError


async def _create_cv(file_type: str = "pdf") -> UploadedCV:
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
        await session.flush()

        cv = UploadedCV(
            profile_id=profile.id,
            file_key="cvs/test/fake.pdf",
            file_type=file_type,
            size_bytes=10,
            status=CVStatus.PENDING,
        )
        session.add(cv)
        await session.commit()
        await session.refresh(cv)
        return cv


async def _fetch(cv_id: uuid.UUID) -> UploadedCV:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        cv = await session.get(UploadedCV, cv_id)
        assert cv is not None
        return cv


def _ctx(storage: FakeStorage, llm_factory: FakeLLMFactory) -> dict[str, Any]:
    return {"sessionmaker": get_sessionmaker(), "storage": storage, "llm_factory": llm_factory}


async def test_parse_cv_job_success() -> None:
    cv = await _create_cv()
    parsed = {"full_name": "Jane Doe", "skills": [{"name": "Python", "category": None}]}
    pdf_bytes = make_pdf_bytes("Jane Doe - Backend Engineer")

    await parse_cv_job(_ctx(FakeStorage(pdf_bytes), FakeLLMFactory(json.dumps(parsed))), str(cv.id))

    refreshed = await _fetch(cv.id)
    assert refreshed.status == CVStatus.PARSED
    assert refreshed.parsed_json == parsed
    assert refreshed.raw_text is not None
    assert "Jane Doe" in refreshed.raw_text
    assert refreshed.error_message is None


async def test_parse_cv_job_marks_failed_on_invalid_llm_json() -> None:
    cv = await _create_cv()
    pdf_bytes = make_pdf_bytes("Jane Doe")

    await parse_cv_job(_ctx(FakeStorage(pdf_bytes), FakeLLMFactory("not valid json")), str(cv.id))

    refreshed = await _fetch(cv.id)
    assert refreshed.status == CVStatus.FAILED
    assert refreshed.error_message is not None


async def test_parse_cv_job_marks_failed_for_legacy_doc_format() -> None:
    cv = await _create_cv(file_type="doc")

    await parse_cv_job(_ctx(FakeStorage(b"legacy doc bytes"), FakeLLMFactory("{}")), str(cv.id))

    refreshed = await _fetch(cv.id)
    assert refreshed.status == CVStatus.FAILED
    assert refreshed.error_message is not None
    assert "doc" in refreshed.error_message.lower()


async def test_parse_cv_job_ignores_missing_cv() -> None:
    ctx = _ctx(FakeStorage(b"irrelevant"), FakeLLMFactory("{}"))
    await parse_cv_job(ctx, str(uuid.uuid4()))  # must not raise
