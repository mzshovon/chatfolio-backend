import re
import tomllib
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

import structlog
from fastapi import BackgroundTasks

from chatfolio.notifications.base import SmsSender

logger = structlog.get_logger()

_TEMPLATES_PATH = Path(__file__).resolve().parent.parent / "notifications" / "sms_templates.toml"

# Local format: 11 digits, 01 + operator digit (3-9: 013/014/015 Teletalk/Banglalink/…, 016 Airtel,
# 017 Grameenphone, 018 Robi, 019 Banglalink) + 8 more. Also accepts +880 / 880 prefixes.
_BD_MOBILE = re.compile(r"^(?:\+?880|0)(1[3-9]\d{8})$")


def resolve_candidate_mobile(profile_phone: str | None, account_phone: str | None) -> str | None:
    """Number to text a candidate on: the profile phone if it is a valid Bangladeshi mobile,
    otherwise the account phone. Logs which source was used (or why none was)."""
    for source, phone in (("profile", profile_phone), ("account", account_phone)):
        mobile = normalize_bd_mobile(phone)
        if mobile:
            logger.info("sms.number_resolved", source=source, to=mask_mobile(mobile))
            return mobile
        logger.info("sms.number_unusable", source=source, present=bool(phone))
    return None


def mask_mobile(mobile: str) -> str:
    return f"{mobile[:5]}{'*' * max(len(mobile) - 8, 0)}{mobile[-3:]}"


class SmsTemplate(StrEnum):
    WELCOME = "WELCOME"
    RECRUITER_ALERT = "RECRUITER_ALERT"
    MEETING_ALERT = "MEETING_ALERT"
    OTP_VERIFICATION = "OTP_VERIFICATION"
    SUBSCRIPTION_EXPIRING = "SUBSCRIPTION_EXPIRING"


@lru_cache
def _load_templates() -> dict[str, str]:
    with _TEMPLATES_PATH.open("rb") as f:
        return tomllib.load(f)["templates"]


def normalize_bd_mobile(raw: str | None) -> str | None:
    """Returns the gateway format (880XXXXXXXXXX) for a valid Bangladeshi mobile number, else None."""
    if not raw:
        return None
    print(f"raw: {raw}")
    match = _BD_MOBILE.fullmatch(re.sub(r"[\s-]", "", raw))
    return f"880{match.group(1)}" if match else None


class SmsService:
    """Renders the configured templates and delivers them through the SMS gateway. Sending is
    queued on FastAPI's BackgroundTasks so it never blocks (or fails) the request."""

    def __init__(self, sender: SmsSender) -> None:
        self._sender = sender

    @staticmethod
    def render(template: SmsTemplate, **params: object) -> str:
        return _load_templates()[template.value].format(**params)

    def queue(
        self,
        background_tasks: BackgroundTasks,
        template: SmsTemplate,
        phone: str | None,
        *,
        campaign_name: str | None = None,
        **params: object,
    ) -> None:
        print(f"phone: {phone}")
        mobile = normalize_bd_mobile(phone)
        if mobile is None:
            logger.warning(
                "sms.skipped_invalid_or_missing_number",
                template=template.value,
                has_phone=bool(phone),
            )
            return
        logger.info("sms.queued", template=template.value, to=mask_mobile(mobile))
        background_tasks.add_task(self._deliver, template, mobile, campaign_name, params)

    async def _deliver(
        self, template: SmsTemplate, mobile: str, campaign_name: str | None, params: dict
    ) -> None:
        # Swallow everything: a gateway outage must never surface as a failed request/task.
        to = mask_mobile(mobile)
        try:
            message = self.render(template, **params)
            logger.info("sms.sending", template=template.value, to=to, chars=len(message))
            await self._sender.send(to=mobile, message=message, campaign_name=campaign_name)
            logger.info("sms.sent", template=template.value, to=to)
        except Exception:
            logger.exception("sms.send_failed", template=template.value, to=to)
