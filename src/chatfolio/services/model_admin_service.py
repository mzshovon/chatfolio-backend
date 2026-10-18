import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.config.settings import LLMProviderName, LLMSettings
from chatfolio.core.exceptions import ConflictError, NotFoundError, ValidationFailedError
from chatfolio.entitlements.cache import EntitlementCache
from chatfolio.models.billing import LLMModel
from chatfolio.models.user import User
from chatfolio.schemas.admin_billing import (
    LLMModelCreateRequest,
    LLMModelResponse,
    LLMModelUpdateRequest,
)
from chatfolio.services.audit import record_admin_action


class ModelAdminService:
    """Admin management of the selectable-model catalogue (metadata only; keys stay in env)."""

    def __init__(
        self, session: AsyncSession, cache: EntitlementCache, llm_settings: LLMSettings
    ) -> None:
        self._session = session
        self._cache = cache
        self._llm_settings = llm_settings

    async def list_models(self) -> list[LLMModelResponse]:
        rows = (
            await self._session.execute(
                select(LLMModel).order_by(LLMModel.sort_order, LLMModel.display_name)
            )
        ).scalars()
        return [self._to_response(row) for row in rows]

    async def create_model(self, admin: User, payload: LLMModelCreateRequest) -> LLMModelResponse:
        model = LLMModel(**payload.model_dump())
        self._session.add(model)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError(
                f"{payload.provider.value}/{payload.model_id} is already registered."
            ) from exc
        await record_admin_action(
            self._session,
            admin,
            "model.create",
            "llm_model",
            model.id,
            {"provider": payload.provider.value, "model_id": payload.model_id},
        )
        await self._commit_and_bump()
        return self._to_response(model)

    async def update_model(
        self, admin: User, model_id: uuid.UUID, payload: LLMModelUpdateRequest
    ) -> LLMModelResponse:
        model = await self._model(model_id)
        updates = payload.model_dump(exclude_unset=True)
        for field in ("display_name", "is_active", "supports_json_mode", "sort_order"):
            if field in updates and updates[field] is None:
                raise ValidationFailedError(f"{field} cannot be null.")

        changes: dict[str, Any] = {}
        for field, value in updates.items():
            old = getattr(model, field)
            if old != value:
                changes[field] = {"from": _plain(old), "to": _plain(value)}
                setattr(model, field, value)

        if changes:
            await self._session.flush()
            await record_admin_action(
                self._session, admin, "model.update", "llm_model", model.id, {"changes": changes}
            )
            await self._commit_and_bump()
        return self._to_response(model)

    async def delete_model(self, admin: User, model_id: uuid.UUID) -> None:
        model = await self._model(model_id)
        await record_admin_action(
            self._session,
            admin,
            "model.delete",
            "llm_model",
            model.id,
            {"provider": model.provider.value, "model_id": model.model_id},
        )
        await self._session.delete(model)
        await self._session.flush()
        await self._commit_and_bump()

    async def _model(self, model_id: uuid.UUID) -> LLMModel:
        model = await self._session.get(LLMModel, model_id)
        if model is None:
            raise NotFoundError("Model not found.")
        return model

    async def _commit_and_bump(self) -> None:
        await self._session.commit()
        await self._cache.bump_catalog_version()

    def _to_response(self, model: LLMModel) -> LLMModelResponse:
        response = LLMModelResponse.model_validate(model)
        response.provider_configured = self._provider_configured(model.provider)
        # Read-only: true when this row is the same model the env configures as the system
        # default (LLM_DEFAULT_PROVIDER + LLM_<PROVIDER>_MODEL). The default itself is config.
        response.is_fallback = (
            model.provider == self._llm_settings.default_provider
            and model.model_id == self._llm_settings.model_for(model.provider)
        )
        return response

    def _provider_configured(self, provider: LLMProviderName) -> bool:
        return self._llm_settings.api_key_for(provider) is not None


def _plain(value: Any) -> Any:
    return str(value) if hasattr(value, "as_tuple") else getattr(value, "value", value)
