import uuid

from fastapi import APIRouter, status

from chatfolio.api.deps import AdminUserDep, DbSessionDep, EntitlementCacheDep, SettingsDep
from chatfolio.schemas.admin_billing import (
    FeatureResponse,
    LLMModelCreateRequest,
    LLMModelResponse,
    LLMModelUpdateRequest,
    PlanAdminResponse,
    PlanCreateRequest,
    PlanFeaturesUpdateRequest,
    PlanModelsUpdateRequest,
    PlanUpdateRequest,
)
from chatfolio.services.model_admin_service import ModelAdminService
from chatfolio.services.plan_admin_service import PlanAdminService

router = APIRouter(prefix="/admin", tags=["admin-pricing"])


def _plans(session: DbSessionDep, cache: EntitlementCacheDep) -> PlanAdminService:
    return PlanAdminService(session, cache)


def _models(
    session: DbSessionDep, cache: EntitlementCacheDep, settings: SettingsDep
) -> ModelAdminService:
    return ModelAdminService(session, cache, settings.llm)


@router.get("/features", response_model=list[FeatureResponse])
async def list_features(
    current_user: AdminUserDep, session: DbSessionDep, cache: EntitlementCacheDep
) -> list[FeatureResponse]:
    """The code-defined feature catalogue (read-only: each feature needs an enforcement hook)."""
    features = await _plans(session, cache).list_features()
    return [
        FeatureResponse(
            key=f.key,
            kind=f.kind.value,
            value_type=f.value_type.value,
            default_period=f.default_period.value if f.default_period else None,
            description=f.description,
        )
        for f in features
    ]


@router.get("/plans", response_model=list[PlanAdminResponse])
async def list_plans(
    current_user: AdminUserDep, session: DbSessionDep, cache: EntitlementCacheDep
) -> list[PlanAdminResponse]:
    return await _plans(session, cache).list_plans()


@router.post("/plans", response_model=PlanAdminResponse, status_code=status.HTTP_201_CREATED)
async def create_plan(
    payload: PlanCreateRequest,
    current_user: AdminUserDep,
    session: DbSessionDep,
    cache: EntitlementCacheDep,
) -> PlanAdminResponse:
    return await _plans(session, cache).create_plan(current_user, payload)


@router.get("/plans/{plan_id}", response_model=PlanAdminResponse)
async def get_plan(
    plan_id: uuid.UUID,
    current_user: AdminUserDep,
    session: DbSessionDep,
    cache: EntitlementCacheDep,
) -> PlanAdminResponse:
    return await _plans(session, cache).get_plan(plan_id)


@router.patch("/plans/{plan_id}", response_model=PlanAdminResponse)
async def update_plan(
    plan_id: uuid.UUID,
    payload: PlanUpdateRequest,
    current_user: AdminUserDep,
    session: DbSessionDep,
    cache: EntitlementCacheDep,
) -> PlanAdminResponse:
    return await _plans(session, cache).update_plan(current_user, plan_id, payload)


@router.delete("/plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(
    plan_id: uuid.UUID,
    current_user: AdminUserDep,
    session: DbSessionDep,
    cache: EntitlementCacheDep,
) -> None:
    await _plans(session, cache).delete_plan(current_user, plan_id)


@router.put("/plans/{plan_id}/features", response_model=PlanAdminResponse)
async def update_plan_features(
    plan_id: uuid.UUID,
    payload: PlanFeaturesUpdateRequest,
    current_user: AdminUserDep,
    session: DbSessionDep,
    cache: EntitlementCacheDep,
) -> PlanAdminResponse:
    return await _plans(session, cache).update_features(current_user, plan_id, payload)


@router.put("/plans/{plan_id}/models", response_model=PlanAdminResponse)
async def set_plan_models(
    plan_id: uuid.UUID,
    payload: PlanModelsUpdateRequest,
    current_user: AdminUserDep,
    session: DbSessionDep,
    cache: EntitlementCacheDep,
) -> PlanAdminResponse:
    return await _plans(session, cache).set_plan_models(current_user, plan_id, payload)


@router.get("/models", response_model=list[LLMModelResponse])
async def list_models(
    current_user: AdminUserDep,
    session: DbSessionDep,
    cache: EntitlementCacheDep,
    settings: SettingsDep,
) -> list[LLMModelResponse]:
    return await _models(session, cache, settings).list_models()


@router.post("/models", response_model=LLMModelResponse, status_code=status.HTTP_201_CREATED)
async def create_model(
    payload: LLMModelCreateRequest,
    current_user: AdminUserDep,
    session: DbSessionDep,
    cache: EntitlementCacheDep,
    settings: SettingsDep,
) -> LLMModelResponse:
    return await _models(session, cache, settings).create_model(current_user, payload)


@router.patch("/models/{model_id}", response_model=LLMModelResponse)
async def update_model(
    model_id: uuid.UUID,
    payload: LLMModelUpdateRequest,
    current_user: AdminUserDep,
    session: DbSessionDep,
    cache: EntitlementCacheDep,
    settings: SettingsDep,
) -> LLMModelResponse:
    return await _models(session, cache, settings).update_model(current_user, model_id, payload)


@router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(
    model_id: uuid.UUID,
    current_user: AdminUserDep,
    session: DbSessionDep,
    cache: EntitlementCacheDep,
    settings: SettingsDep,
) -> None:
    await _models(session, cache, settings).delete_model(current_user, model_id)
