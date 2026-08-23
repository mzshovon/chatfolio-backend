import httpx
import structlog

from chatfolio.config.settings import SmsSettings

logger = structlog.get_logger()


class VendorSmsSender:
    """Generic REST adapter for a vendor exposing a single POST endpoint that accepts
    {"to", "message", "sender_id"} JSON with a bearer-token API key. Swap the payload shape and
    auth header here if your vendor's actual contract differs."""

    def __init__(self, settings: SmsSettings) -> None:
        self._settings = settings

    async def send(self, *, to: str, message: str) -> None:
        if not self._settings.api_url:
            logger.warning("sms.vendor_not_configured", to=to)
            return

        headers = {}
        if self._settings.api_key:
            headers["Authorization"] = f"Bearer {self._settings.api_key.get_secret_value()}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self._settings.api_url,
                json={"to": to, "message": message, "sender_id": self._settings.sender_id},
                headers=headers,
            )
            response.raise_for_status()
