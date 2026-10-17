from datetime import datetime
from typing import Any

import httpx
import structlog

from chatfolio.billing.strategy import PurchaseVerification, StrategyName
from chatfolio.config.settings import SupportKoriSettings
from chatfolio.core.exceptions import ServiceUnavailableError

logger = structlog.get_logger(__name__)

_UNAVAILABLE = "Activation is temporarily unavailable. Please try again shortly."


class SupportKoriLicenseStrategy:
    """Validates a license key with Support Kori's `POST /api/licenses/verify`.

    Request `{"product_id", "license_key"}`; response `{"valid", "product", "purchased_at",
    "uses", "seats"}`. There is no expiry, plan id or "redeemed" signal in it, so expiry is ours
    and "already used" is enforced from our own `licenses` table; `uses`/`seats` are ignored.
    """

    name = StrategyName.SUPPORT_KORI_LICENSE

    def __init__(
        self, settings: SupportKoriSettings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        self._transport = transport  # tests inject a mock transport; None = real network

    async def verify(self, *, credential: str, product_ref: str) -> PurchaseVerification:
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds, transport=self._transport
            ) as client:
                response = await client.post(
                    self._settings.verify_url,
                    json={"product_id": product_ref, "license_key": credential},
                )
        except httpx.HTTPError as exc:
            logger.warning("support_kori.unreachable", error=type(exc).__name__)
            raise ServiceUnavailableError(_UNAVAILABLE) from exc

        if response.status_code >= 500:
            logger.warning("support_kori.server_error", status=response.status_code)
            raise ServiceUnavailableError(_UNAVAILABLE)
        if response.status_code in (401, 403):
            # Our credentials/product are wrong - every buyer would be told "invalid license".
            logger.error("support_kori.rejected_our_request", status=response.status_code)
            raise ServiceUnavailableError(_UNAVAILABLE)
        if response.status_code != 200:
            # Any other 4xx: Support Kori is saying this key is not good.
            return PurchaseVerification(valid=False, raw={"http_status": response.status_code})

        try:
            body: dict[str, Any] = response.json()
        except ValueError:
            logger.warning("support_kori.unparseable_response")
            raise ServiceUnavailableError(_UNAVAILABLE) from None
        if not isinstance(body, dict):
            raise ServiceUnavailableError(_UNAVAILABLE)

        return PurchaseVerification(
            valid=body.get("valid") is True,
            purchased_at=_parse_time(body.get("purchased_at")),
            raw=body,
        )


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
