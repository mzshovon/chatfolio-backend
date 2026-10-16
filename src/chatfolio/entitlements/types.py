from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from chatfolio.entitlements.catalog import UNLIMITED, FeatureKey
from chatfolio.models.billing import QuotaPeriod


@dataclass(frozen=True)
class FeatureValue:
    int_value: int | None = None
    bool_value: bool | None = None
    period: QuotaPeriod | None = None


@dataclass(frozen=True)
class Entitlements:
    """What one user may do right now. Immutable and JSON-serialisable so it can sit in Redis.

    Missing/mistyped features fail **closed** (0 / False) — a catalogue gap must never grant
    access."""

    plan_slug: str
    plan_name: str
    # "free" (no active subscription) or the SubscriptionSource value that granted the plan.
    source: str
    subscription_expires_at: datetime | None
    # Earliest moment any part of this snapshot stops being true (subscription or an override
    # expiring). Past it, the snapshot must be recomputed — also caps the Redis TTL.
    valid_until: datetime | None
    values: Mapping[str, FeatureValue]

    def _value(self, key: FeatureKey) -> FeatureValue:
        return self.values.get(key.value, FeatureValue())

    def limit(self, key: FeatureKey) -> int:
        """The numeric limit; UNLIMITED (-1) means no cap. Missing => 0 (deny)."""
        value = self._value(key).int_value
        return 0 if value is None else value

    def is_unlimited(self, key: FeatureKey) -> bool:
        return self.limit(key) == UNLIMITED

    def enabled(self, key: FeatureKey) -> bool:
        return bool(self._value(key).bool_value)

    def period(self, key: FeatureKey) -> QuotaPeriod | None:
        return self._value(key).period

    def to_json(self) -> dict[str, Any]:
        return {
            "plan_slug": self.plan_slug,
            "plan_name": self.plan_name,
            "source": self.source,
            "subscription_expires_at": _iso(self.subscription_expires_at),
            "valid_until": _iso(self.valid_until),
            "values": {
                key: {
                    "int_value": v.int_value,
                    "bool_value": v.bool_value,
                    "period": v.period.value if v.period else None,
                }
                for key, v in self.values.items()
            },
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "Entitlements":
        return cls(
            plan_slug=data["plan_slug"],
            plan_name=data["plan_name"],
            source=data["source"],
            subscription_expires_at=_parse(data["subscription_expires_at"]),
            valid_until=_parse(data["valid_until"]),
            values={
                key: FeatureValue(
                    int_value=v["int_value"],
                    bool_value=v["bool_value"],
                    period=QuotaPeriod(v["period"]) if v["period"] else None,
                )
                for key, v in data["values"].items()
            },
        )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
