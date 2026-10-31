import json
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import structlog
from redis.asyncio import Redis

from chatfolio.config.settings import GeoIPSettings
from chatfolio.core.client_ip import is_public_ip
from chatfolio.schemas.viewer import ViewerLocationResponse

logger = structlog.get_logger(__name__)

_CACHE_PREFIX = "geoip:"


def format_utc_offset(timezone: str | None, now: datetime | None = None) -> str | None:
    """Label like "GMT+6", "GMT+5:30", "GMT-3:30" or "GMT" for an IANA zone, DST-aware."""
    if not timezone:
        return None
    try:
        offset = (now or datetime.now(UTC)).astimezone(ZoneInfo(timezone)).utcoffset()
    except Exception:
        return None
    if offset is None:
        return None
    minutes = int(offset.total_seconds() // 60)
    if minutes == 0:
        return "GMT"
    sign = "+" if minutes > 0 else "-"
    hours, rest = divmod(abs(minutes), 60)
    return f"GMT{sign}{hours}" + (f":{rest:02d}" if rest else "")


def _build(
    city: str | None, country: str | None, country_code: str | None, timezone: str | None
) -> ViewerLocationResponse:
    utc_offset = format_utc_offset(timezone)
    place = ", ".join(part for part in (city, country) if part)
    display = " ".join(part for part in (place, f"({utc_offset})" if utc_offset else "") if part)
    return ViewerLocationResponse(
        city=city,
        country=country,
        country_code=country_code,
        timezone=timezone,
        utc_offset=utc_offset,
        display=display or None,
    )


class GeoLocationService:
    """IP -> location via an HTTP GeoIP provider, cached per IP in Redis. Never raises: a failed
    lookup is an unknown location, not an error page for the visitor."""

    def __init__(
        self,
        settings: GeoIPSettings,
        redis: Redis,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._redis = redis
        self._transport = transport  # tests inject a mock transport; None = real network

    async def locate(self, ip: str | None) -> ViewerLocationResponse:
        if not self._settings.enabled or ip is None or not is_public_ip(ip):
            return ViewerLocationResponse()

        key = f"{_CACHE_PREFIX}{ip}"
        try:
            cached = await self._redis.get(key)
        except Exception:
            logger.warning("geoip.cache_read_failed")
            cached = None
        if cached:
            data = json.loads(cached)
            return _build(data["city"], data["country"], data["country_code"], data["timezone"])

        fetched = await self._fetch(ip)
        if fetched is None:
            return ViewerLocationResponse()
        city, country, country_code, timezone = fetched
        try:
            await self._redis.set(
                key,
                json.dumps(
                    {
                        "city": city,
                        "country": country,
                        "country_code": country_code,
                        "timezone": timezone,
                    }
                ),
                ex=self._settings.cache_ttl_seconds,
            )
        except Exception:
            logger.warning("geoip.cache_write_failed")
        return _build(city, country, country_code, timezone)

    async def _fetch(self, ip: str) -> tuple[str | None, str | None, str | None, str | None] | None:
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.timeout_seconds, transport=self._transport
            ) as client:
                response = await client.get(self._settings.lookup_url.format(ip=ip))
            body: Any = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("geoip.lookup_failed", error=type(exc).__name__)
            return None
        if (
            response.status_code != 200
            or not isinstance(body, dict)
            or body.get("success") is False
        ):
            logger.warning("geoip.lookup_rejected", status=response.status_code)
            return None

        tz = body.get("timezone")
        return (
            body.get("city") or None,
            body.get("country") or None,
            body.get("country_code") or None,
            (tz.get("id") if isinstance(tz, dict) else None) or None,
        )
