import uuid
from collections.abc import Sequence
from typing import TypeVar

from sqlalchemy import select

from chatfolio.models.mixins import ProfileChildMixin
from chatfolio.models.profile import CandidateProfile
from chatfolio.repositories.base import BaseRepository

ChildT = TypeVar("ChildT", bound=ProfileChildMixin)


class ProfileRepository(BaseRepository):
    async def get_by_id(self, profile_id: uuid.UUID) -> CandidateProfile | None:
        return await self.session.get(CandidateProfile, profile_id)

    async def get_by_user_id(self, user_id: uuid.UUID) -> CandidateProfile | None:
        result = await self.session.execute(
            select(CandidateProfile).where(CandidateProfile.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def create(self, profile: CandidateProfile) -> CandidateProfile:
        self.session.add(profile)
        await self.session.flush()
        return profile

    async def save(self, profile: CandidateProfile) -> CandidateProfile:
        await self.session.flush()
        return profile

    async def list_children(self, model: type[ChildT], profile_id: uuid.UUID) -> Sequence[ChildT]:
        result = await self.session.execute(select(model).where(model.profile_id == profile_id))
        return result.scalars().all()

    async def get_child(
        self, model: type[ChildT], profile_id: uuid.UUID, child_id: uuid.UUID
    ) -> ChildT | None:
        result = await self.session.execute(
            select(model).where(model.id == child_id, model.profile_id == profile_id)
        )
        return result.scalar_one_or_none()

    async def add_child(self, child: ChildT) -> ChildT:
        self.session.add(child)
        await self.session.flush()
        return child

    async def save_child(self, child: ChildT) -> ChildT:
        await self.session.flush()
        return child

    async def delete_child(self, child: ChildT) -> None:
        await self.session.delete(child)
        await self.session.flush()
