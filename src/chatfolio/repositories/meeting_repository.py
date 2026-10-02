import uuid
from datetime import datetime

from sqlalchemy import func, select, text

from chatfolio.models.meeting import GoogleCalendarConnection, MeetingRequestRecord
from chatfolio.repositories.base import BaseRepository


class MeetingRepository(BaseRepository):
    async def get_by_user_id(self, user_id: uuid.UUID) -> GoogleCalendarConnection | None:
        result = await self.session.execute(
            select(GoogleCalendarConnection).where(GoogleCalendarConnection.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def create(self, connection: GoogleCalendarConnection) -> GoogleCalendarConnection:
        self.session.add(connection)
        await self.session.flush()
        return connection

    async def save(self, connection: GoogleCalendarConnection) -> GoogleCalendarConnection:
        await self.session.flush()
        return connection

    async def delete(self, connection: GoogleCalendarConnection) -> None:
        await self.session.delete(connection)
        await self.session.flush()

    async def lock_candidate_requests(self, user_id: uuid.UUID) -> None:
        """Serialises concurrent public meeting requests for one candidate until the transaction
        ends, so two different sessions can't both pass the cool-down check at once."""
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": user_id.int % (2**63)}
        )

    async def session_has_request(self, chat_session_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            select(MeetingRequestRecord.id).where(
                MeetingRequestRecord.chat_session_id == chat_session_id
            )
        )
        return result.first() is not None

    async def latest_request_at(self, user_id: uuid.UUID) -> datetime | None:
        result = await self.session.execute(
            select(func.max(MeetingRequestRecord.created_at)).where(
                MeetingRequestRecord.user_id == user_id
            )
        )
        return result.scalar_one()

    async def record_request(
        self, chat_session_id: uuid.UUID, user_id: uuid.UUID
    ) -> MeetingRequestRecord:
        record = MeetingRequestRecord(chat_session_id=chat_session_id, user_id=user_id)
        self.session.add(record)
        await self.session.flush()
        return record
