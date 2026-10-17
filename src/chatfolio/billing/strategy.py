"""How a plan gets paid for. Two strategies are planned, behind one seam:

  * `support_kori_license` (now) - Support Kori sells and settles; the buyer enters a license key
    and we validate it with their API. A valid license = payment done.
  * `native_gateway` (later)     - our own checkout. Its webhook ends in the same
    `BillingService.grant_from_purchase`, so nothing downstream (orders, subscriptions,
    entitlements, admin) knows or cares which strategy sold the plan.

A strategy only answers "is this purchase genuine?" - granting the plan is shared code.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol


class StrategyName(StrEnum):
    SUPPORT_KORI_LICENSE = "support_kori_license"
    NATIVE_GATEWAY = "native_gateway"


# Strategies that exist in code today; the others can be listed but not switched on.
IMPLEMENTED_STRATEGIES = frozenset({StrategyName.SUPPORT_KORI_LICENSE})

STRATEGY_LABELS: dict[StrategyName, str] = {
    StrategyName.SUPPORT_KORI_LICENSE: "License key",
    StrategyName.NATIVE_GATEWAY: "Online payment",
}


@dataclass(frozen=True)
class PurchaseVerification:
    valid: bool
    purchased_at: datetime | None = None
    # The seller's raw answer, kept on the order for audit. Never contains the credential.
    raw: dict[str, Any] = field(default_factory=dict)


class ActivationStrategy(Protocol):
    """A strategy where the buyer presents a credential (a license key) we can check at the
    seller. `verify` raises ServiceUnavailableError when the seller can't be reached - callers
    must never treat an outage as "invalid"."""

    name: StrategyName

    async def verify(self, *, credential: str, product_ref: str) -> PurchaseVerification: ...
