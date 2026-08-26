import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.core.exceptions import ConflictError, NotFoundError
from chatfolio.models.rbac import Permission, Role

DEFAULT_PAGE_SIZE = 20


class RbacService:
    """Admin-manageable Roles/Permissions CRUD — see Role/Permission's model docstrings for why
    this is deliberately not wired into real authorization."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_roles(
        self, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> list[Role]:
        result = await self._session.execute(
            select(Role).order_by(Role.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def create_role(
        self, name: str, description: str, permissions: list[str]
    ) -> Role:
        existing = await self._session.execute(select(Role).where(Role.name == name))
        if existing.scalar_one_or_none() is not None:
            raise ConflictError(f"A role named {name!r} already exists.")

        role = Role(name=name, description=description, permissions=permissions)
        self._session.add(role)
        await self._session.flush()
        return role

    async def update_role(self, role_id: uuid.UUID, updates: dict[str, object]) -> Role:
        role = await self._get_role(role_id)

        new_name = updates.get("name")
        if isinstance(new_name, str) and new_name != role.name:
            existing = await self._session.execute(select(Role).where(Role.name == new_name))
            if existing.scalar_one_or_none() is not None:
                raise ConflictError(f"A role named {new_name!r} already exists.")

        for field, value in updates.items():
            setattr(role, field, value)
        await self._session.flush()
        return role

    async def delete_role(self, role_id: uuid.UUID) -> None:
        role = await self._get_role(role_id)
        await self._session.delete(role)
        await self._session.flush()

    async def list_permissions(
        self, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> list[tuple[Permission, int]]:
        result = await self._session.execute(
            select(Permission).order_by(Permission.created_at.desc()).limit(limit).offset(offset)
        )
        permissions = list(result.scalars().all())
        usage = await self._usage_counts()
        return [(p, usage.get(p.key, 0)) for p in permissions]

    async def create_permission(self, key: str, description: str) -> tuple[Permission, int]:
        existing = await self._session.execute(select(Permission).where(Permission.key == key))
        if existing.scalar_one_or_none() is not None:
            raise ConflictError(f"Permission key {key!r} already exists.")

        permission = Permission(key=key, description=description)
        self._session.add(permission)
        await self._session.flush()
        return permission, 0

    async def update_permission(
        self, permission_id: uuid.UUID, updates: dict[str, object]
    ) -> tuple[Permission, int]:
        permission = await self._get_permission(permission_id)
        for field, value in updates.items():
            setattr(permission, field, value)
        await self._session.flush()
        usage = await self._usage_counts()
        return permission, usage.get(permission.key, 0)

    async def delete_permission(self, permission_id: uuid.UUID) -> None:
        permission = await self._get_permission(permission_id)

        # Matches the frontend's existing confirm-dialog copy ("will be removed from any roles
        # that grant it") rather than blocking the delete while any role still holds it. Plain
        # `JSON` columns don't support a portable containment query, so this filters in Python —
        # fine at admin scale (a handful of roles).
        roles_result = await self._session.execute(select(Role))
        for role in roles_result.scalars().all():
            if permission.key in role.permissions:
                role.permissions = [p for p in role.permissions if p != permission.key]

        await self._session.delete(permission)
        await self._session.flush()

    async def _get_role(self, role_id: uuid.UUID) -> Role:
        role = await self._session.get(Role, role_id)
        if role is None:
            raise NotFoundError("Role not found.")
        return role

    async def _get_permission(self, permission_id: uuid.UUID) -> Permission:
        permission = await self._session.get(Permission, permission_id)
        if permission is None:
            raise NotFoundError("Permission not found.")
        return permission

    async def _usage_counts(self) -> dict[str, int]:
        result = await self._session.execute(select(Role.permissions))
        counts: dict[str, int] = {}
        for (permissions,) in result.all():
            for key in permissions or []:
                counts[key] = counts.get(key, 0) + 1
        return counts
