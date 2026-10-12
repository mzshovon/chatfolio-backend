import httpx
import structlog

from chatfolio.config.settings import SmsSettings

logger = structlog.get_logger()

DEFAULT_CAMPAIGN_NAME = "default"
# Every message we send is transactional, never promotional.
TRANSACTION_TYPE = "T"


class VendorSmsSender:
    """Adapter for the SMS gateway's `POST /sms/send` endpoint: client id/password go in
    X-Client-Id / X-Client-Password headers, the body carries campaignName, transactionType,
    mobileNumber (880XXXXXXXXXX) and message."""

    def __init__(self, settings: SmsSettings) -> None:
        self._settings = settings

    async def send(self, *, to: str, message: str, campaign_name: str | None = None) -> None:
        cfg = self._settings
        missing = [
            name
            for name, value in (
                ("SMS_API_URL", cfg.api_url),
                ("SMS_CLIENT_ID", cfg.client_id),
                ("SMS_CLIENT_PASSWORD", cfg.client_password),
            )
            if not value
        ]
        if missing:
            logger.warning("sms.vendor_not_configured", missing=missing)
            return

        headers = {
            "X-Client-Id": self._settings.client_id,
            "X-Client-Password": self._settings.client_password.get_secret_value(),
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self._settings.api_url,
                json={
                    "campaignName": campaign_name or DEFAULT_CAMPAIGN_NAME,
                    "transactionType": TRANSACTION_TYPE,
                    "mobileNumber": to,
                    "message": message,
                },
                headers=headers,
            )
        logger.info(
            "sms.vendor_response",
            status_code=response.status_code,
            body=response.text[:500],
        )
        response.raise_for_status()
