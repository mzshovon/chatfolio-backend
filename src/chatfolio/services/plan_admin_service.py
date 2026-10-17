import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.core.exceptions import ConflictError, NotFoundError, ValidationFailedError
from chatfolio.entitlements.cache import EntitlementCache
from chatfolio.entitlements.catalog import FEATURES, FEATURES_BY_KEY, validate_feature_value
from chatfolio.models.billing import (
    DurationUnit,
    Feature,
    LLMModel,
    Plan,
    PlanFeature,
    PlanModel,
    QuotaPeriod,
    Subscription,
    SubscriptionStatus,
)
from chatfolio.models.user import User
from chatfolio.schemas.admin_billing import (
    PlanAdminResponse,
    PlanCreateRequest,
    PlanFeaturesUpdateRequest,
    PlanFeatureValue,
    PlanModelsUpdateRequest,
    PlanUpdateRequest,
)
from chatfolio.services.audit import record_admin_action

# Fields on Plan that a PATCH may set to null; everything else must keep a real value.
_NULLABLE_PLAN_FIELDS = frozenset({"external_product_ref", "badge_text", "purchase_url"})


class PlanAdminService:
    """Admin management of the pricing catalogue. Every mutation writes an audit row, commits,
    and *then* bumps the catalogue cache version — committing first matters: bumping before the
    commit would let a concurrent request re-cache the old data under the new version."""

    def __init__(self, session: AsyncSession, cache: EntitlementCache) -> None:
        self._session = session
        self._cache = cache

    # ---- reads ----

    async def list_features(self) -> list[Feature]:
        return list((await self._session.execute(select(Feature).order_by(Feature.key))).scalars())

    async def list_plans(self) -> list[PlanAdminResponse]:
        plans = (
            (await self._session.execute(select(Plan).order_by(Plan.sort_order, Plan.created_at)))
            .scalars()
            .all()
        )
        return [await self._to_response(plan) for plan in plans]

    async def get_plan(self, plan_id: uuid.UUID) -> PlanAdminResponse:
        return await self._to_response(await self._plan(plan_id))

    # ---- plans ----

    async def create_plan(self, admin: User, payload: PlanCreateRequest) -> PlanAdminResponse:
        if await self._slug_taken(payload.slug):
            raise ConflictError(f"A plan with slug {payload.slug!r} already exists.")

        source: Plan | None = None
        if payload.clone_from:
            source = (
                await self._session.execute(select(Plan).where(Plan.slug == payload.clone_from))
            ).scalar_one_or_none()
            if source is None:
                raise NotFoundError(f"Plan {payload.clone_from!r} to clone was not found.")

        plan = Plan(
            **payload.model_dump(exclude={"slug", "clone_from"}),
            slug=payload.slug,
            is_default_free=False,
        )
        values = {f.feature_key: f for f in source.features} if source is not None else {}
        # Complete matrix from day one: anything not cloned starts closed (0 / off) so a new plan
        # can never accidentally grant something the admin hasn't decided on.
        plan.features = [
            _closed_or_copied(definition.key.value, values.get(definition.key.value))
            for definition in FEATURES
        ]
        self._session.add(plan)
        await self._session.flush()

        if source is not None:
            model_ids = (
                await self._session.execute(
                    select(PlanModel.model_id).where(PlanModel.plan_id == source.id)
                )
            ).scalars()
            for model_id in model_ids:
                self._session.add(PlanModel(plan_id=plan.id, model_id=model_id))
        await record_admin_action(
            self._session,
            admin,
            "plan.create",
            "plan",
            plan.id,
            {"slug": plan.slug, "cloned_from": payload.clone_from},
        )
        await self._commit_and_bump()
        return await self._to_response(plan)

    async def update_plan(
        self, admin: User, plan_id: uuid.UUID, payload: PlanUpdateRequest
    ) -> PlanAdminResponse:
        plan = await self._plan(plan_id)
        updates = payload.model_dump(exclude_unset=True)
        for field, value in updates.items():
            if value is None and field not in _NULLABLE_PLAN_FIELDS:
                raise ValidationFailedError(f"{field} cannot be null.")

        if plan.is_default_free:
            if updates.get("is_active") is False:
                raise ValidationFailedError("The default free plan cannot be deactivated.")
            if "duration_unit" in updates or "duration_count" in updates:
                raise ValidationFailedError("The default free plan has no duration.")

        unit = updates.get("duration_unit", plan.duration_unit)
        count = updates.get("duration_count", plan.duration_count)
        if unit == DurationUnit.LIFETIME and count != 0:
            raise ValidationFailedError("A lifetime plan must have duration_count 0.")
        if unit != DurationUnit.LIFETIME and count < 1:
            raise ValidationFailedError("duration_count must be at least 1.")

        changes: dict[str, Any] = {}
        for field, value in updates.items():
            old = getattr(plan, field)
            if old != value:
                changes[field] = {"from": _plain(old), "to": _plain(value)}
                setattr(plan, field, value)
        if changes:
            await record_admin_action(
                self._session, admin, "plan.update", "plan", plan.id, {"changes": changes}
            )
            await self._commit_and_bump()
        return await self._to_response(plan)

    async def delete_plan(self, admin: User, plan_id: uuid.UUID) -> None:
        plan = await self._plan(plan_id)
        if plan.is_default_free:
            raise ValidationFailedError("The default free plan cannot be deleted.")
        subscribers = (
            await self._session.execute(
                select(func.count())
                .select_from(Subscription)
                .where(Subscription.plan_id == plan.id)
            )
        ).scalar_one()
        if subscribers:
            raise ConflictError(
                "This plan has subscription history and cannot be deleted; deactivate it instead."
            )
        await record_admin_action(
            self._session, admin, "plan.delete", "plan", plan.id, {"slug": plan.slug}
        )
        await self._session.delete(plan)
        await self._session.flush()
        await self._commit_and_bump()

    # ---- feature values ----

    async def update_features(
        self, admin: User, plan_id: uuid.UUID, payload: PlanFeaturesUpdateRequest
    ) -> PlanAdminResponse:
        plan = await self._plan(plan_id)
        current = {f.feature_key: f for f in plan.features}

        errors: list[str] = []
        for key, value in payload.features.items():
            definition = FEATURES_BY_KEY.get(key)
            if definition is None:
                errors.append(f"Unknown feature {key!r}.")
                continue
            problem = validate_feature_value(
                definition, value.int_value, value.bool_value, value.period
            )
            if problem:
                errors.append(problem)
        if errors:
            raise ValidationFailedError(" ".join(errors))

        changes: dict[str, Any] = {}
        for key, value in payload.features.items():
            definition = FEATURES_BY_KEY[key]
            period: QuotaPeriod | None = value.period
            if period is None and definition.default_period is not None:
                # Keep a flow feature's existing period unless the admin sets a new one.
                existing = current.get(key)
                period = (
                    existing.period if existing and existing.period else definition.default_period
                )
            row = current.get(key)
            before = _feature_dict(row)
            if row is None:
                row = PlanFeature(plan_id=plan.id, feature_key=key)
                plan.features.append(row)
            row.int_value = value.int_value
            row.bool_value = value.bool_value
            row.period = period
            after = _feature_dict(row)
            if before != after:
                changes[key] = {"from": before, "to": after}

        if changes:
            await record_admin_action(
                self._session, admin, "plan.features.update", "plan", plan.id, {"changes": changes}
            )
            await self._commit_and_bump()
        return await self._to_response(plan)

    async def set_plan_models(
        self, admin: User, plan_id: uuid.UUID, payload: PlanModelsUpdateRequest
    ) -> PlanAdminResponse:
        plan = await self._plan(plan_id)
        wanted = set(payload.model_ids)
        if wanted:
            found = set(
                (
                    await self._session.execute(select(LLMModel.id).where(LLMModel.id.in_(wanted)))
                ).scalars()
            )
            if found != wanted:
                raise ValidationFailedError("One or more model ids do not exist.")

        current = set(
            (
                await self._session.execute(
                    select(PlanModel.model_id).where(PlanModel.plan_id == plan.id)
                )
            ).scalars()
        )
        for model_id in wanted - current:
            self._session.add(PlanModel(plan_id=plan.id, model_id=model_id))
        for model_id in current - wanted:
            row = await self._session.get(PlanModel, (plan.id, model_id))
            if row is not None:
                await self._session.delete(row)
        if wanted != current:
            await self._session.flush()
            await record_admin_action(
                self._session,
                admin,
                "plan.models.update",
                "plan",
                plan.id,
                {
                    "added": sorted(map(str, wanted - current)),
                    "removed": sorted(map(str, current - wanted)),
                },
            )
            await self._commit_and_bump()
        return await self._to_response(plan)

    # ---- helpers ----

    async def _plan(self, plan_id: uuid.UUID) -> Plan:
        plan = await self._session.get(Plan, plan_id)
        if plan is None:
            raise NotFoundError("Plan not found.")
        return plan

    async def _slug_taken(self, slug: str) -> bool:
        return (
            await self._session.execute(select(Plan.id).where(Plan.slug == slug))
        ).first() is not None

    async def _commit_and_bump(self) -> None:
        await self._session.commit()
        await self._cache.bump_catalog_version()

    async def _to_response(self, plan: Plan) -> PlanAdminResponse:
        # Server-side defaults/onupdate (updated_at) are expired after a flush; reload everything
        # here, in async context, rather than letting the response builder lazy-load.
        await self._session.refresh(plan)
        await self._session.refresh(plan, ["features"])
        model_ids = list(
            (
                await self._session.execute(
                    select(PlanModel.model_id).where(PlanModel.plan_id == plan.id)
                )
            ).scalars()
        )
        subscribers = (
            await self._session.execute(
                select(func.count())
                .select_from(Subscription)
                .where(
                    Subscription.plan_id == plan.id,
                    Subscription.status == SubscriptionStatus.ACTIVE,
                )
            )
        ).scalar_one()
        return PlanAdminResponse(
            id=plan.id,
            slug=plan.slug,
            name=plan.name,
            tagline=plan.tagline,
            is_active=plan.is_active,
            is_public=plan.is_public,
            is_default_free=plan.is_default_free,
            sort_order=plan.sort_order,
            price_minor=plan.price_minor,
            currency=plan.currency,
            duration_unit=plan.duration_unit,
            duration_count=plan.duration_count,
            external_product_ref=plan.external_product_ref,
            trial_days=plan.trial_days,
            purchase_url=plan.purchase_url,
            allowed_strategies=list(plan.allowed_strategies),
            badge_text=plan.badge_text,
            cta_label=plan.cta_label,
            footnote=plan.footnote,
            marketing_bullets=list(plan.marketing_bullets),
            features={
                f.feature_key: PlanFeatureValue(
                    int_value=f.int_value, bool_value=f.bool_value, period=f.period
                )
                for f in plan.features
            },
            model_ids=sorted(model_ids, key=str),
            active_subscribers=subscribers,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
        )


def _closed_or_copied(key: str, source: PlanFeature | None) -> PlanFeature:
    if source is not None:
        return PlanFeature(
            feature_key=key,
            int_value=source.int_value,
            bool_value=source.bool_value,
            period=source.period,
        )
    definition = FEATURES_BY_KEY[key]
    if definition.value_type.value == "int":
        return PlanFeature(feature_key=key, int_value=0, period=definition.default_period)
    return PlanFeature(feature_key=key, bool_value=False)


def _feature_dict(row: PlanFeature | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "int_value": row.int_value,
        "bool_value": row.bool_value,
        "period": row.period.value if row.period else None,
    }


def _plain(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value
