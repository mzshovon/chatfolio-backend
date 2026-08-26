import uuid

from fastapi import APIRouter, Query, status

from chatfolio.api.deps import AdminUserDep, DbSessionDep
from chatfolio.models.rbac import Permission, Role
from chatfolio.schemas.admin import (
    PermissionCreateRequest,
    PermissionResponse,
    PermissionUpdateRequest,
    RoleCreateRequest,
    RoleResponse,
    RoleUpdateRequest,
)
from chatfolio.services.rbac_service import RbacService

router = APIRouter(prefix="/admin", tags=["admin"])


def _service(session: DbSessionDep) -> RbacService:
    return RbacService(session)


def _to_permission_response(permission: Permission, used_by_roles_count: int) -> PermissionResponse:
    return PermissionResponse(
        id=permission.id,
        key=permission.key,
        description=permission.description,
        used_by_roles_count=used_by_roles_count,
    )


@router.get("/roles", response_model=list[RoleResponse])
async def list_roles(
    current_user: AdminUserDep,
    session: DbSessionDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[Role]:
    return await _service(session).list_roles(limit=limit, offset=offset)


@router.post("/roles", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
async def create_role(
    payload: RoleCreateRequest, current_user: AdminUserDep, session: DbSessionDep
) -> Role:
    return await _service(session).create_role(
        payload.name, payload.description, payload.permissions
    )


@router.patch("/roles/{role_id}", response_model=RoleResponse)
async def update_role(
    role_id: uuid.UUID,
    payload: RoleUpdateRequest,
    current_user: AdminUserDep,
    session: DbSessionDep,
) -> Role:
    updates = payload.model_dump(exclude_unset=True)
    return await _service(session).update_role(role_id, updates)


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: uuid.UUID, current_user: AdminUserDep, session: DbSessionDep
) -> None:
    await _service(session).delete_role(role_id)


@router.get("/permissions", response_model=list[PermissionResponse])
async def list_permissions(
    current_user: AdminUserDep,
    session: DbSessionDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[PermissionResponse]:
    pairs = await _service(session).list_permissions(limit=limit, offset=offset)
    return [_to_permission_response(p, count) for p, count in pairs]


@router.post("/permissions", response_model=PermissionResponse, status_code=status.HTTP_201_CREATED)
async def create_permission(
    payload: PermissionCreateRequest, current_user: AdminUserDep, session: DbSessionDep
) -> PermissionResponse:
    permission, count = await _service(session).create_permission(
        payload.key, payload.description
    )
    return _to_permission_response(permission, count)


@router.patch("/permissions/{permission_id}", response_model=PermissionResponse)
async def update_permission(
    permission_id: uuid.UUID,
    payload: PermissionUpdateRequest,
    current_user: AdminUserDep,
    session: DbSessionDep,
) -> PermissionResponse:
    updates = payload.model_dump(exclude_unset=True)
    permission, count = await _service(session).update_permission(permission_id, updates)
    return _to_permission_response(permission, count)


@router.delete("/permissions/{permission_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_permission(
    permission_id: uuid.UUID, current_user: AdminUserDep, session: DbSessionDep
) -> None:
    await _service(session).delete_permission(permission_id)
