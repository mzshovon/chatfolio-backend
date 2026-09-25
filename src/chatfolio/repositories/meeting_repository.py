import uuid

from sqlalchemy import select

from chatfolio.models.meeting import GoogleCalendarConnection
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
