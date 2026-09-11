from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.models.feedback import Feedback

DEFAULT_PAGE_SIZE = 20


class FeedbackService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def submit(self, nps_score: int, message: str | None) -> Feedback:
        feedback = Feedback(nps_score=nps_score, message=message)
        self._session.add(feedback)
        await self._session.flush()
        return feedback

    async def list_feedback(
        self, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> list[Feedback]:
        result = await self._session.execute(
            select(Feedback).order_by(Feedback.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())
