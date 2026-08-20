import uuid
from typing import TypeVar

from chatfolio.core.exceptions import NotFoundError
from chatfolio.models.mixins import ProfileChildMixin
from chatfolio.models.profile import CandidateProfile
from chatfolio.models.user import User
from chatfolio.repositories.profile_repository import ProfileRepository
from chatfolio.schemas.profile import ProfileUpdateRequest

ChildT = TypeVar("ChildT", bound=ProfileChildMixin)


class ProfileService:
    def __init__(self, repository: ProfileRepository) -> None:
        self._repository = repository

    async def get_or_create_for_user(self, user: User) -> CandidateProfile:
        profile = await self._repository.get_by_user_id(user.id)
        if profile is None:
            profile = await self._repository.create(CandidateProfile(user_id=user.id))
        return profile

    async def update(self, user: User, payload: ProfileUpdateRequest) -> CandidateProfile:
        profile = await self.get_or_create_for_user(user)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(profile, field, value)
        return await self._repository.save(profile)

    async def list_children(self, user: User, model: type[ChildT]) -> list[ChildT]:
        profile = await self.get_or_create_for_user(user)
        return list(await self._repository.list_children(model, profile.id))

    async def add_child(self, user: User, child: ChildT) -> ChildT:
        profile = await self.get_or_create_for_user(user)
        child.profile_id = profile.id
        return await self._repository.add_child(child)

    async def update_child(
        self, user: User, model: type[ChildT], child_id: uuid.UUID, updates: dict[str, object]
    ) -> ChildT:
        child = await self.get_owned_child(user, model, child_id)
        for field, value in updates.items():
            setattr(child, field, value)
        return await self._repository.save_child(child)

    async def delete_child(self, user: User, model: type[ChildT], child_id: uuid.UUID) -> None:
        child = await self.get_owned_child(user, model, child_id)
        await self._repository.delete_child(child)

    async def get_owned_child(self, user: User, model: type[ChildT], child_id: uuid.UUID) -> ChildT:
        profile = await self.get_or_create_for_user(user)
        child = await self._repository.get_child(model, profile.id, child_id)
        if child is None:
            raise NotFoundError(f"{model.__name__} not found.")
        return child
