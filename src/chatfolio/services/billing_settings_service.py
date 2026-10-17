from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.billing.strategy import IMPLEMENTED_STRATEGIES, StrategyName
from chatfolio.core.exceptions import ValidationFailedError
from chatfolio.entitlements.cache import EntitlementCache
from chatfolio.models.billing import AppSetting
from chatfolio.models.user import User
from chatfolio.services.audit import record_admin_action

_KEY = "billing"
_CACHE_NAME = "billing_settings"


@dataclass(frozen=True)
class BillingSettings:
    enabled_strategies: list[str] = field(
        default_factory=lambda: [StrategyName.SUPPORT_KORI_LICENSE.value]
    )
    # Reject license keys bought more than this many days ago (stops hoarded keys). None = off.
    max_activation_delay_days: int | None = None


class BillingSettingsService:
    """Admin-editable switches for how plans can be bought. Stored in `app_settings` so flipping
    a strategy (e.g. turning the native gateway on later) needs no deploy."""

    def __init__(self, session: AsyncSession, cache: EntitlementCache) -> None:
        self._session = session
        self._cache = cache

    async def get(self) -> BillingSettings:
        cached = await self._cache.get_catalog(_CACHE_NAME)
        if cached is not None:
            return BillingSettings(**cached)
        row = await self._session.get(AppSetting, _KEY)
        settings = BillingSettings(**row.value) if row else BillingSettings()
        await self._cache.set_catalog(_CACHE_NAME, _as_dict(settings))
        return settings

    async def update(
        self,
        admin: User,
        *,
        enabled_strategies: list[str],
        max_activation_delay_days: int | None,
    ) -> BillingSettings:
        unknown = [s for s in enabled_strategies if s not in {n.value for n in StrategyName}]
        if unknown:
            raise ValidationFailedError(f"Unknown strategies: {', '.join(unknown)}.")
        not_built = [s for s in enabled_strategies if StrategyName(s) not in IMPLEMENTED_STRATEGIES]
        if not_built:
            raise ValidationFailedError(f"Not available yet: {', '.join(not_built)}.")

        before = await self.get()
        updated = BillingSettings(
            enabled_strategies=sorted(set(enabled_strategies)),
            max_activation_delay_days=max_activation_delay_days,
        )
        row = await self._session.get(AppSetting, _KEY)
        if row is None:
            row = AppSetting(key=_KEY, value=_as_dict(updated))
            self._session.add(row)
        else:
            row.value = _as_dict(updated)
        await self._session.flush()
        await record_admin_action(
            self._session,
            admin,
            "billing.settings.update",
            "app_setting",
            _KEY,
            {"from": _as_dict(before), "to": _as_dict(updated)},
        )
        await self._session.commit()
        await self._cache.bump_catalog_version()
        return updated


def _as_dict(settings: BillingSettings) -> dict[str, object]:
    return {
        "enabled_strategies": list(settings.enabled_strategies),
        "max_activation_delay_days": settings.max_activation_delay_days,
    }
