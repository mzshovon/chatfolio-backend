from collections.abc import Callable, Iterator

import httpx
import pytest

from chatfolio.api.deps import get_geolocation_service
from chatfolio.config.settings import GeoIPSettings, get_settings
from chatfolio.core.redis import get_redis
from chatfolio.main import app
from chatfolio.services.geolocation_service import GeoLocationService, format_utc_offset
from tests.factories.publish_flow import authed_client

DHAKA = {
    "success": True,
    "city": "Dhaka",
    "country": "Bangladesh",
    "country_code": "BD",
    "timezone": {"id": "Asia/Dhaka", "utc": "+06:00"},
}


@pytest.fixture
def geo(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., list[str]]]:
    """Installs a GeoLocationService on a mock provider; returns the IPs it was asked about."""
    monkeypatch.setattr(get_settings().geoip, "trust_proxy_headers", True)
    lookups: list[str] = []

    def install(body: dict | None = None, status: int = 200, enabled: bool = True) -> list[str]:
        def handler(request: httpx.Request) -> httpx.Response:
            lookups.append(request.url.path.lstrip("/"))
            return httpx.Response(status, json=body if body is not None else DHAKA)

        service = GeoLocationService(
            GeoIPSettings(enabled=enabled), get_redis(), httpx.MockTransport(handler)
        )
        app.dependency_overrides[get_geolocation_service] = lambda: service
        return lookups

    yield install
    app.dependency_overrides.pop(get_geolocation_service, None)


async def _locate(ip: str | None) -> httpx.Response:
    client, _ = await authed_client("viewer@example.com")
    headers = {"X-Forwarded-For": ip} if ip else {}
    return await client.get("/api/v1/public/viewer/location", headers=headers)


async def test_resolves_public_ip(geo: Callable[..., list[str]]) -> None:
    lookups = geo()
    ip = "8.8.8.50"
    await get_redis().delete(f"geoip:{ip}")

    response = await _locate(f"{ip}, 10.0.0.1")  # left-most hop is the real client

    assert response.status_code == 200
    assert response.json() == {
        "city": "Dhaka",
        "country": "Bangladesh",
        "country_code": "BD",
        "timezone": "Asia/Dhaka",
        "utc_offset": "GMT+6",
        "display": "Dhaka, Bangladesh (GMT+6)",
    }
    assert response.headers["cache-control"] == "private, max-age=300"
    assert lookups == [ip]


async def test_second_lookup_is_served_from_cache(geo: Callable[..., list[str]]) -> None:
    lookups = geo()
    ip = "8.8.8.51"
    await get_redis().delete(f"geoip:{ip}")

    await _locate(ip)
    second = await _locate(ip)

    assert second.json()["display"] == "Dhaka, Bangladesh (GMT+6)"
    assert lookups == [ip]


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "192.168.0.9", "::1", "not-an-ip"])
async def test_private_or_invalid_ip_is_unknown_without_lookup(
    geo: Callable[..., list[str]], ip: str
) -> None:
    lookups = geo()

    response = await _locate(ip)

    assert response.status_code == 200
    assert all(value is None for value in response.json().values())
    assert lookups == []


async def test_without_proxy_trust_forwarded_header_is_ignored(
    geo: Callable[..., list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    lookups = geo()
    monkeypatch.setattr(get_settings().geoip, "trust_proxy_headers", False)

    response = await _locate("8.8.8.52")  # spoof attempt; the socket peer is not public

    assert response.json()["display"] is None
    assert lookups == []


async def test_provider_failure_degrades_to_unknown(geo: Callable[..., list[str]]) -> None:
    geo(body={"success": False, "message": "Invalid IP address"}, status=200)
    await get_redis().delete("geoip:8.8.8.53")

    response = await _locate("8.8.8.53")

    assert response.status_code == 200
    assert response.json()["display"] is None


async def test_provider_5xx_degrades_to_unknown(geo: Callable[..., list[str]]) -> None:
    geo(body={}, status=503)
    await get_redis().delete("geoip:8.8.8.54")

    assert (await _locate("8.8.8.54")).json()["display"] is None


async def test_disabled_makes_no_lookup(geo: Callable[..., list[str]]) -> None:
    lookups = geo(enabled=False)

    response = await _locate("8.8.8.55")

    assert response.json()["display"] is None
    assert lookups == []


async def test_country_only_when_city_missing(geo: Callable[..., list[str]]) -> None:
    geo(body={**DHAKA, "city": ""})
    await get_redis().delete("geoip:8.8.8.56")

    assert (await _locate("8.8.8.56")).json()["display"] == "Bangladesh (GMT+6)"


def test_format_utc_offset() -> None:
    assert format_utc_offset("Asia/Dhaka") == "GMT+6"
    assert format_utc_offset("Asia/Kolkata") == "GMT+5:30"
    assert format_utc_offset("America/St_Johns") in ("GMT-3:30", "GMT-2:30")  # DST-dependent
    assert format_utc_offset("UTC") == "GMT"
    assert format_utc_offset("Not/AZone") is None
    assert format_utc_offset(None) is None
