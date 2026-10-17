import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update

from chatfolio.api.deps import get_activation_strategies, get_settings
from chatfolio.billing.strategy import PurchaseVerification, StrategyName
from chatfolio.billing.support_kori import SupportKoriLicenseStrategy
from chatfolio.config.settings import SupportKoriSettings
from chatfolio.config.settings import get_settings as real_get_settings
from chatfolio.core.exceptions import ServiceUnavailableError
from chatfolio.core.redis import get_redis
from chatfolio.db.session import get_sessionmaker
from chatfolio.entitlements.cache import EntitlementCache
from chatfolio.main import app
from chatfolio.models.billing import (
    License,
    LicenseStatus,
    Order,
    OrderStatus,
    Plan,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
)
from chatfolio.models.user import TwoFactorMethod, User, UserRole
from chatfolio.workers.jobs_billing import (
    expire_subscriptions_job,
    expiry_reminder_job,
    revalidate_licenses_job,
)
from tests.conftest import fake_email_sender, fake_sms_sender
from tests.factories.plans import grant_plan, user_id_of
from tests.factories.publish_flow import authed_client

ACTIVATE = "/api/v1/billing/activate"
PRODUCT_ID = "prod_chatfolio"
PURCHASED = datetime(2026, 10, 16, 7, 56, 27, tzinfo=UTC)


class FakeKori:
    """Stands in for Support Kori: knows which keys exist, and records every call made to it."""

    name = StrategyName.SUPPORT_KORI_LICENSE

    def __init__(self) -> None:
        self.keys: dict[str, datetime] = {}
        self.revoked: set[str] = set()
        self.down = False
        self.calls: list[tuple[str, str]] = []

    def sell(self, key: str, purchased_at: datetime | None = None) -> str:
        self.keys[key] = purchased_at or datetime.now(UTC)
        return key

    async def verify(self, *, credential: str, product_ref: str) -> PurchaseVerification:
        self.calls.append((credential, product_ref))
        if self.down:
            raise ServiceUnavailableError("seller down")
        valid = credential in self.keys and credential not in self.revoked
        return PurchaseVerification(
            valid=valid,
            purchased_at=self.keys.get(credential) if valid else None,
            raw={"valid": valid, "product": "Chatfolio", "uses": 0, "seats": 1},
        )


@pytest.fixture
async def kori() -> AsyncGenerator[FakeKori]:
    fake = FakeKori()
    settings = real_get_settings().model_copy(deep=True)
    settings.support_kori.product_id = PRODUCT_ID
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_activation_strategies] = lambda: {fake.name: fake}
    yield fake
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(get_activation_strategies, None)


async def _admin(email: str) -> tuple[httpx.AsyncClient, dict[str, str]]:
    client, headers = await authed_client(email)
    async with get_sessionmaker()() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        user.role = UserRole.ADMIN
        await session.commit()
    return client, headers


async def _activate(
    client: httpx.AsyncClient, headers: dict[str, str], key: str, **extra: Any
) -> httpx.Response:
    return await client.post(ACTIVATE, headers=headers, json={"license_key": key, **extra})


async def _state(client: httpx.AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    return (await client.get("/api/v1/billing/subscription", headers=headers)).json()


async def _plan_slug(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    return (await client.get("/api/v1/me/entitlements", headers=headers)).json()["plan"]["slug"]


# ---------- the Support Kori HTTP client ----------


def _kori_client(handler: Any) -> SupportKoriLicenseStrategy:
    return SupportKoriLicenseStrategy(
        SupportKoriSettings(verify_url="https://kori.test/api/licenses/verify"),
        transport=httpx.MockTransport(handler),
    )


async def test_kori_client_sends_the_documented_request_and_parses_the_documented_reply() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "valid": True,
                "product": "Chatfolio - No domain, No hosting & let your cv talks for you",
                "purchased_at": "2026-10-16T07:56:27.469Z",
                "uses": 0,
                "seats": 1,
            },
        )

    result = await _kori_client(handler).verify(credential="BUYERS-KEY", product_ref="p1")

    assert str(seen[0].url) == "https://kori.test/api/licenses/verify"
    assert json.loads(seen[0].content) == {"product_id": "p1", "license_key": "BUYERS-KEY"}
    assert result.valid is True
    assert result.purchased_at == datetime(2026, 10, 16, 7, 56, 27, 469000, tzinfo=UTC)


@pytest.mark.parametrize(
    ("status", "body", "expect_valid"),
    [(200, {"valid": False}, False), (200, {}, False), (404, {"error": "nope"}, False)],
)
async def test_kori_client_treats_rejections_as_invalid(
    status: int, body: dict[str, Any], expect_valid: bool
) -> None:
    client = _kori_client(lambda request: httpx.Response(status, json=body))
    assert (await client.verify(credential="K" * 8, product_ref="p")).valid is expect_valid


@pytest.mark.parametrize("status", [401, 403, 500, 502, 503])
async def test_kori_client_never_reports_an_outage_or_misconfiguration_as_invalid(
    status: int,
) -> None:
    client = _kori_client(lambda request: httpx.Response(status, json={}))
    with pytest.raises(ServiceUnavailableError):
        await client.verify(credential="K" * 8, product_ref="p")


async def test_kori_client_survives_network_errors_and_garbage() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with pytest.raises(ServiceUnavailableError):
        await _kori_client(boom).verify(credential="K" * 8, product_ref="p")
    with pytest.raises(ServiceUnavailableError):
        await _kori_client(lambda r: httpx.Response(200, text="<html>")).verify(
            credential="K" * 8, product_ref="p"
        )


# ---------- activating a license ----------


async def test_activation_requires_authentication() -> None:
    client, _ = await authed_client("bill-anon@example.com")
    assert (await client.post(ACTIVATE, json={"license_key": "ABCDEFGH"})).status_code == 401


async def test_valid_license_unlocks_premium_for_the_plan_duration(kori: FakeKori) -> None:
    client, headers = await authed_client("bill-ok@example.com")
    key = kori.sell("BUYERS-KEY-0001")

    response = await _activate(client, headers, key)

    assert response.status_code == 201, response.text
    current = response.json()["current"]
    assert current["plan_slug"] == "premium" and current["source"] == "support_kori_license"
    ends = datetime.fromisoformat(current["expires_at"])
    assert timedelta(days=178) < ends - datetime.now(UTC) < timedelta(days=186)  # 6 months
    assert await _plan_slug(client, headers) == "premium"
    assert kori.calls == [(key, PRODUCT_ID)]

    async with get_sessionmaker()() as session:
        order = (await session.execute(select(Order))).scalar_one()
        license_ = (await session.execute(select(License))).scalar_one()
    assert order.status == OrderStatus.PAID and order.amount_minor == 50000
    assert order.raw_response is not None and "BUYERS" not in json.dumps(order.raw_response)
    assert license_.key_last4 == "0001"
    assert key not in license_.license_key_encrypted  # reversible only with the Fernet key
    assert license_.license_key_hash != key


async def test_the_plan_comes_from_the_server_not_the_clients_claims(kori: FakeKori) -> None:
    client, headers = await authed_client("bill-claims@example.com")
    key = kori.sell("BUYERS-KEY-0002")

    response = await _activate(
        client, headers, key, price_minor=1, duration_count=999, expires_at="2099-01-01T00:00:00Z"
    )

    assert response.status_code == 201
    ends = datetime.fromisoformat(response.json()["current"]["expires_at"])
    assert ends - datetime.now(UTC) < timedelta(days=186)


async def test_resubmitting_your_own_live_key_is_idempotent(kori: FakeKori) -> None:
    client, headers = await authed_client("bill-idem@example.com")
    key = kori.sell("BUYERS-KEY-0003")
    await _activate(client, headers, key)

    again = await _activate(client, headers, key)

    assert again.status_code == 201
    assert len(again.json()["history"]) == 1
    assert len(kori.calls) == 1  # answered from our own records
    async with get_sessionmaker()() as session:
        assert len((await session.execute(select(Order))).scalars().all()) == 1


async def test_an_expired_key_can_never_be_reused_even_though_the_seller_still_says_valid(
    kori: FakeKori,
) -> None:
    client, headers = await authed_client("bill-expired@example.com")
    key = kori.sell("BUYERS-KEY-0004")
    await _activate(client, headers, key)
    calls_before = len(kori.calls)

    async with get_sessionmaker()() as session:  # 6 months and a day later
        await session.execute(
            update(Subscription).values(expires_at=datetime.now(UTC) - timedelta(days=1))
        )
        await session.commit()
    await EntitlementCache(get_redis()).flush_all()

    response = await _activate(client, headers, key)

    assert response.status_code == 422
    assert "already been used" in response.json()["detail"]
    assert len(kori.calls) == calls_before  # our DB decided; the seller wasn't even asked
    assert await _plan_slug(client, headers) == "free"


async def test_a_key_belongs_to_one_account_and_others_learn_nothing(kori: FakeKori) -> None:
    owner, owner_headers = await authed_client("bill-own@example.com")
    thief, thief_headers = await authed_client("bill-thief@example.com")
    key = kori.sell("BUYERS-KEY-0005")
    await _activate(owner, owner_headers, key)

    stolen = await _activate(thief, thief_headers, key)
    wrong = await _activate(thief, thief_headers, "NOT-A-REAL-KEY")

    assert stolen.status_code == wrong.status_code == 422
    assert stolen.json() == wrong.json()  # indistinguishable from a wrong key
    assert await _plan_slug(thief, thief_headers) == "free"
    assert await _plan_slug(owner, owner_headers) == "premium"


async def test_two_accounts_racing_for_one_key_get_exactly_one_winner(kori: FakeKori) -> None:
    a, a_headers = await authed_client("bill-race-a@example.com")
    b, b_headers = await authed_client("bill-race-b@example.com")
    key = kori.sell("BUYERS-KEY-0006")

    first, second = await asyncio.gather(_activate(a, a_headers, key), _activate(b, b_headers, key))

    assert sorted([first.status_code, second.status_code]) == [201, 422]
    async with get_sessionmaker()() as session:
        assert len((await session.execute(select(License))).scalars().all()) == 1


async def test_invalid_key_is_rejected_and_an_outage_is_not_blamed_on_the_user(
    kori: FakeKori,
) -> None:
    client, headers = await authed_client("bill-bad@example.com")

    bad = await _activate(client, headers, "TOTALLY-WRONG-KEY")
    kori.down = True
    outage = await _activate(client, headers, kori.sell("BUYERS-KEY-0007"))

    assert bad.status_code == 422
    assert outage.status_code == 503
    assert await _plan_slug(client, headers) == "free"
    async with get_sessionmaker()() as session:
        assert (await session.execute(select(License))).first() is None  # nothing half-claimed
    kori.down = False
    assert (await _activate(client, headers, "BUYERS-KEY-0007")).status_code == 201  # retry works


async def test_buying_the_same_plan_again_stacks_onto_the_current_term(kori: FakeKori) -> None:
    client, headers = await authed_client("bill-stack@example.com")
    first = (await _activate(client, headers, kori.sell("BUYERS-KEY-0008"))).json()["current"]

    second = (await _activate(client, headers, kori.sell("BUYERS-KEY-0009"))).json()

    first_end = datetime.fromisoformat(first["expires_at"])
    second_end = datetime.fromisoformat(second["current"]["expires_at"])
    assert timedelta(days=178) < second_end - first_end < timedelta(days=186)
    assert sorted(h["status"] for h in second["history"]) == ["active", "cancelled"]


async def test_bad_keys_are_throttled_per_account_even_for_a_later_valid_key(
    kori: FakeKori,
) -> None:
    client, headers = await authed_client("bill-throttle@example.com")
    settings = real_get_settings().model_copy(deep=True)
    settings.support_kori.product_id = PRODUCT_ID
    settings.support_kori.max_failed_attempts_per_hour = 3
    app.dependency_overrides[get_settings] = lambda: settings
    good = kori.sell("BUYERS-KEY-0010")

    for n in range(3):
        assert (await _activate(client, headers, f"GUESS-NUMBER-{n}")).status_code == 422
    blocked = await _activate(client, headers, good)

    assert blocked.status_code == 429
    assert kori.calls[-1][0] != good  # never even reached the seller


async def test_plan_selection_rules(kori: FakeKori) -> None:
    client, headers = await authed_client("bill-plans@example.com")
    admin, admin_headers = await _admin("bill-plans-admin@example.com")
    key = kori.sell("BUYERS-KEY-0011")

    assert (await _activate(client, headers, key, plan_slug="free")).status_code == 404
    assert (await _activate(client, headers, key, plan_slug="ghost")).status_code == 404

    # A second sellable plan makes the choice ambiguous when the client doesn't name one.
    await admin.post(
        "/api/v1/admin/plans",
        headers=admin_headers,
        json={"slug": "yearly", "name": "Yearly", "duration_unit": "year", "duration_count": 1},
    )
    ambiguous = await _activate(client, headers, key)
    assert ambiguous.status_code == 422
    assert (await _activate(client, headers, key, plan_slug="yearly")).status_code == 201


async def test_per_plan_product_ref_overrides_the_env_default(kori: FakeKori) -> None:
    client, headers = await authed_client("bill-ref@example.com")
    admin, admin_headers = await _admin("bill-ref-admin@example.com")
    premium = next(
        p
        for p in (await admin.get("/api/v1/admin/plans", headers=admin_headers)).json()
        if p["slug"] == "premium"
    )
    await admin.patch(
        f"/api/v1/admin/plans/{premium['id']}",
        headers=admin_headers,
        json={"external_product_ref": "kori_prod_premium_6m"},
    )

    await _activate(client, headers, kori.sell("BUYERS-KEY-0012"))

    assert kori.calls == [("BUYERS-KEY-0012", "kori_prod_premium_6m")]


async def test_activation_without_any_product_id_fails_closed(kori: FakeKori) -> None:
    client, headers = await authed_client("bill-noproduct@example.com")
    settings = real_get_settings().model_copy(deep=True)
    settings.support_kori.product_id = None
    app.dependency_overrides[get_settings] = lambda: settings

    response = await _activate(client, headers, kori.sell("BUYERS-KEY-0013"))

    assert response.status_code == 503 and not kori.calls


async def test_a_plan_can_be_withdrawn_from_a_strategy(kori: FakeKori) -> None:
    client, headers = await authed_client("bill-withdrawn@example.com")
    admin, admin_headers = await _admin("bill-withdrawn-admin@example.com")
    premium = next(
        p
        for p in (await admin.get("/api/v1/admin/plans", headers=admin_headers)).json()
        if p["slug"] == "premium"
    )
    await admin.patch(
        f"/api/v1/admin/plans/{premium['id']}",
        headers=admin_headers,
        json={"allowed_strategies": ["native_gateway"]},
    )

    response = await _activate(client, headers, kori.sell("BUYERS-KEY-0014"), plan_slug="premium")

    assert response.status_code == 404 and not kori.calls


async def test_old_licenses_can_be_refused_by_admin_policy(kori: FakeKori) -> None:
    client, headers = await authed_client("bill-delay@example.com")
    admin, admin_headers = await _admin("bill-delay-admin@example.com")
    await admin.put(
        "/api/v1/admin/billing/settings",
        headers=admin_headers,
        json={"enabled_strategies": ["support_kori_license"], "max_activation_delay_days": 30},
    )

    stale = kori.sell("BUYERS-KEY-0015", datetime.now(UTC) - timedelta(days=60))
    fresh = kori.sell("BUYERS-KEY-0016", datetime.now(UTC) - timedelta(days=2))

    assert (await _activate(client, headers, stale)).status_code == 422
    assert (await _activate(client, headers, fresh)).status_code == 201


# ---------- switching strategies & offers ----------


async def test_strategies_endpoint_lists_what_can_be_bought_and_where(kori: FakeKori) -> None:
    client, headers = await authed_client("bill-strat@example.com")
    admin, admin_headers = await _admin("bill-strat-admin@example.com")
    premium = next(
        p
        for p in (await admin.get("/api/v1/admin/plans", headers=admin_headers)).json()
        if p["slug"] == "premium"
    )
    await admin.patch(
        f"/api/v1/admin/plans/{premium['id']}",
        headers=admin_headers,
        json={"purchase_url": "https://supportkori.com/p/chatfolio"},
    )

    body = (await client.get("/api/v1/billing/strategies", headers=headers)).json()

    assert [s["name"] for s in body["strategies"]] == ["support_kori_license"]
    assert body["strategies"][0]["plans"] == [
        {
            "slug": "premium",
            "name": "Premium",
            "purchase_url": "https://supportkori.com/p/chatfolio",
        }
    ]
    card = next(
        p for p in (await client.get("/api/v1/pricing")).json()["plans"] if p["slug"] == "premium"
    )
    assert card["purchase_url"] == "https://supportkori.com/p/chatfolio"


async def test_disabling_the_strategy_switches_activation_off_everywhere(kori: FakeKori) -> None:
    client, headers = await authed_client("bill-off@example.com")
    admin, admin_headers = await _admin("bill-off-admin@example.com")

    assert (
        await admin.put(
            "/api/v1/admin/billing/settings",
            headers=admin_headers,
            json={"enabled_strategies": [], "max_activation_delay_days": None},
        )
    ).status_code == 200

    assert (await client.get("/api/v1/billing/strategies", headers=headers)).json()[
        "strategies"
    ] == []
    assert (await _activate(client, headers, kori.sell("BUYERS-KEY-0017"))).status_code == 404
    assert not kori.calls


async def test_admin_billing_settings_validation_and_audit(kori: FakeKori) -> None:
    admin, headers = await _admin("bill-settings-admin@example.com")
    url = "/api/v1/admin/billing/settings"

    current = (await admin.get(url, headers=headers)).json()
    assert current["enabled_strategies"] == ["support_kori_license"]
    assert current["available_strategies"] == ["support_kori_license"]

    for bad in (["ghost"], ["native_gateway"]):  # unknown / not built yet
        response = await admin.put(
            url,
            headers=headers,
            json={"enabled_strategies": bad, "max_activation_delay_days": None},
        )
        assert response.status_code == 422, bad
    assert (
        await admin.put(
            url, headers=headers, json={"enabled_strategies": [], "max_activation_delay_days": 0}
        )
    ).status_code == 422


async def test_plan_admin_rejects_non_https_purchase_urls_and_unknown_strategies() -> None:
    admin, headers = await _admin("bill-plan-validate@example.com")
    premium = next(
        p
        for p in (await admin.get("/api/v1/admin/plans", headers=headers)).json()
        if p["slug"] == "premium"
    )
    url = f"/api/v1/admin/plans/{premium['id']}"

    assert (
        await admin.patch(url, headers=headers, json={"purchase_url": "http://x.test"})
    ).status_code == 422
    assert (
        await admin.patch(url, headers=headers, json={"allowed_strategies": ["paypal"]})
    ).status_code == 422


# ---------- trial ----------


async def _verify_phone(
    client: httpx.AsyncClient, headers: dict[str, str], phone: str
) -> uuid.UUID:
    user_id = await user_id_of(client, headers)
    async with get_sessionmaker()() as session:
        user = await session.get(User, user_id)
        assert user is not None
        user.phone, user.two_factor_enabled = phone, True
        user.two_factor_method = TwoFactorMethod.PHONE
        await session.commit()
    return user_id


async def _enable_trial(days: int = 7) -> None:
    async with get_sessionmaker()() as session:
        await session.execute(update(Plan).where(Plan.slug == "premium").values(trial_days=days))
        await session.commit()
    await EntitlementCache(get_redis()).bump_catalog_version()


async def test_trial_needs_a_verified_phone_and_is_once_per_account_and_phone() -> None:
    await _enable_trial()
    unverified, unverified_headers = await authed_client("bill-trial-nophone@example.com")
    assert (
        await unverified.post(
            "/api/v1/billing/trial", headers=unverified_headers, json={"plan_slug": "premium"}
        )
    ).json()["code"] == "PHONE_VERIFICATION_REQUIRED"

    first, first_headers = await authed_client("bill-trial-1@example.com")
    await _verify_phone(first, first_headers, "+8801711111111")
    offers = (await first.get("/api/v1/billing/strategies", headers=first_headers)).json()
    assert offers["trials"] == [{"plan_slug": "premium", "days": 7}]

    started = await first.post(
        "/api/v1/billing/trial", headers=first_headers, json={"plan_slug": "premium"}
    )
    assert started.status_code == 201
    current = started.json()["current"]
    assert current["source"] == "trial"
    ends = datetime.fromisoformat(current["expires_at"])
    assert timedelta(days=6) < ends - datetime.now(UTC) < timedelta(days=8)
    assert await _plan_slug(first, first_headers) == "premium"

    # Never again for this account...
    async with get_sessionmaker()() as session:
        await session.execute(update(Subscription).values(status=SubscriptionStatus.EXPIRED))
        await session.commit()
    await EntitlementCache(get_redis()).flush_all()
    assert (
        await first.post(
            "/api/v1/billing/trial", headers=first_headers, json={"plan_slug": "premium"}
        )
    ).status_code == 409
    assert (await first.get("/api/v1/billing/strategies", headers=first_headers)).json()[
        "trials"
    ] == []

    # ...nor for a second account on the same phone number.
    second, second_headers = await authed_client("bill-trial-2@example.com")
    await _verify_phone(second, second_headers, "+8801711111111")
    assert (
        await second.post(
            "/api/v1/billing/trial", headers=second_headers, json={"plan_slug": "premium"}
        )
    ).status_code == 409


async def test_trial_unavailable_when_plan_has_no_trial_or_user_already_has_a_plan() -> None:
    client, headers = await authed_client("bill-trial-none@example.com")
    uid = await _verify_phone(client, headers, "+8801722222222")
    assert (
        await client.post("/api/v1/billing/trial", headers=headers, json={"plan_slug": "premium"})
    ).status_code == 404  # trial_days is 0

    await _enable_trial()
    await grant_plan(uid)
    assert (
        await client.post("/api/v1/billing/trial", headers=headers, json={"plan_slug": "premium"})
    ).status_code == 409


async def test_buying_a_plan_replaces_a_running_trial(kori: FakeKori) -> None:
    await _enable_trial()
    client, headers = await authed_client("bill-trial-upgrade@example.com")
    await _verify_phone(client, headers, "+8801733333333")
    await client.post("/api/v1/billing/trial", headers=headers, json={"plan_slug": "premium"})

    response = await _activate(client, headers, kori.sell("BUYERS-KEY-0018"))

    assert response.status_code == 201
    ends = datetime.fromisoformat(response.json()["current"]["expires_at"])
    assert ends - datetime.now(UTC) > timedelta(days=170)  # the paid term, not trial + extension


# ---------- admin: orders, licenses, revocation ----------


async def test_admin_can_list_orders_and_licenses_without_ever_seeing_the_key(
    kori: FakeKori,
) -> None:
    client, headers = await authed_client("bill-admin-list@example.com")
    admin, admin_headers = await _admin("bill-admin-list-admin@example.com")
    key = kori.sell("SECRET-KEY-ABCD")
    await _activate(client, headers, key)

    orders = (await admin.get("/api/v1/admin/billing/orders", headers=admin_headers)).json()
    licenses = (await admin.get("/api/v1/admin/billing/licenses", headers=admin_headers)).json()

    assert orders[0]["user_email"] == "bill-admin-list@example.com"
    assert orders[0]["plan_slug"] == "premium" and orders[0]["strategy"] == "support_kori_license"
    assert licenses[0]["key_last4"] == "ABCD" and licenses[0]["status"] == "active"
    assert "SECRET-KEY" not in json.dumps(licenses) + json.dumps(orders)


async def test_admin_revocation_ends_the_plan_and_blocks_reuse(kori: FakeKori) -> None:
    client, headers = await authed_client("bill-revoke@example.com")
    admin, admin_headers = await _admin("bill-revoke-admin@example.com")
    key = kori.sell("BUYERS-KEY-0019")
    await _activate(client, headers, key)
    assert await _plan_slug(client, headers) == "premium"
    license_id = (await admin.get("/api/v1/admin/billing/licenses", headers=admin_headers)).json()[
        0
    ]["id"]

    assert (
        await admin.post(
            f"/api/v1/admin/billing/licenses/{license_id}/revoke", headers=admin_headers
        )
    ).status_code == 204

    assert await _plan_slug(client, headers) == "free"
    assert (await _activate(client, headers, key)).status_code == 422
    assert (
        await admin.post(
            f"/api/v1/admin/billing/licenses/{license_id}/revoke", headers=admin_headers
        )
    ).status_code == 409
    async with get_sessionmaker()() as session:
        assert (await session.execute(select(Order))).scalar_one().status == OrderStatus.REVOKED


# ---------- background jobs ----------


def _ctx(kori: FakeKori | None = None) -> dict[str, Any]:
    settings = real_get_settings().model_copy(deep=True)
    settings.support_kori.product_id = PRODUCT_ID
    return {
        "sessionmaker": get_sessionmaker(),
        "settings": settings,
        "license_strategy": kori,
        "email_sender": fake_email_sender,
        "sms_sender": fake_sms_sender,
    }


async def _activated(
    kori: FakeKori, email: str, key: str
) -> tuple[httpx.AsyncClient, dict[str, str]]:
    client, headers = await authed_client(email)
    assert (await _activate(client, headers, kori.sell(key))).status_code == 201
    return client, headers


async def test_expire_job_marks_ended_subscriptions_without_touching_live_ones(
    kori: FakeKori,
) -> None:
    ended, _ = await _activated(kori, "bill-job-ended@example.com", "BUYERS-KEY-0020")
    await _activated(kori, "bill-job-live@example.com", "BUYERS-KEY-0021")
    async with get_sessionmaker()() as session:
        first = (
            (await session.execute(select(Subscription).order_by(Subscription.created_at)))
            .scalars()
            .first()
        )
        assert first is not None
        first.expires_at = datetime.now(UTC) - timedelta(hours=1)
        await session.commit()

    assert await expire_subscriptions_job(_ctx()) == 1

    async with get_sessionmaker()() as session:
        statuses = sorted(
            s.status.value for s in (await session.execute(select(Subscription))).scalars()
        )
    assert statuses == ["active", "expired"]
    assert ended is not None


async def test_revalidation_revokes_refunded_licenses_but_never_on_an_outage(
    kori: FakeKori,
) -> None:
    refunded, refunded_headers = await _activated(kori, "bill-rv-refund@example.com", "RV-KEY-0001")
    kept, kept_headers = await _activated(kori, "bill-rv-kept@example.com", "RV-KEY-0002")
    outage, outage_headers = await _activated(kori, "bill-rv-outage@example.com", "RV-KEY-0003")
    async with get_sessionmaker()() as session:  # make them all due for a re-check
        await session.execute(
            update(License).values(validated_at=datetime.now(UTC) - timedelta(days=30))
        )
        await session.commit()

    kori.revoked.add("RV-KEY-0001")
    result = await revalidate_licenses_job(_ctx(kori))
    assert result == {"still_valid": 2, "revoked": 1, "unknown": 0}
    assert await _plan_slug(refunded, refunded_headers) == "free"
    assert await _plan_slug(kept, kept_headers) == "premium"

    # Seller unreachable on a later run: nobody is downgraded for it.
    async with get_sessionmaker()() as session:
        await session.execute(
            update(License).values(last_revalidated_at=datetime.now(UTC) - timedelta(days=30))
        )
        await session.commit()
    kori.down = True
    result = await revalidate_licenses_job(_ctx(kori))
    assert result == {"still_valid": 0, "revoked": 0, "unknown": 2}
    assert await _plan_slug(outage, outage_headers) == "premium"


async def test_recently_checked_licenses_are_not_rechecked(kori: FakeKori) -> None:
    await _activated(kori, "bill-rv-fresh@example.com", "RV-KEY-0004")
    calls = len(kori.calls)

    result = await revalidate_licenses_job(_ctx(kori))

    assert result == {"still_valid": 0, "revoked": 0, "unknown": 0}
    assert len(kori.calls) == calls


async def test_expiry_reminder_goes_out_once(kori: FakeKori) -> None:
    await _activated(kori, "bill-remind@example.com", "RM-KEY-0001")
    await _activated(kori, "bill-remind-far@example.com", "RM-KEY-0002")
    async with get_sessionmaker()() as session:
        soonest = (
            (await session.execute(select(Subscription).order_by(Subscription.created_at)))
            .scalars()
            .first()
        )
        assert soonest is not None
        soonest.expires_at = datetime.now(UTC) + timedelta(days=2)
        await session.commit()
    async with get_sessionmaker()() as session:
        user = (
            await session.execute(select(User).where(User.email == "bill-remind@example.com"))
        ).scalar_one()
        user.phone = "01712345678"
        await session.commit()

    assert await expiry_reminder_job(_ctx()) == 1
    assert await expiry_reminder_job(_ctx()) == 0  # not repeated

    [mail] = fake_email_sender.sent
    assert mail["to"] == "bill-remind@example.com" and "Premium" in mail["subject"]
    [sms] = fake_sms_sender.sent
    assert sms["to"] == "8801712345678" and "Premium" in sms["message"]
    async with get_sessionmaker()() as session:
        reminded = [
            s.reminder_sent_at is not None
            for s in (await session.execute(select(Subscription))).scalars()
        ]
    assert sorted(reminded) == [False, True]


async def test_subscription_status_enums_match_the_license_lifecycle() -> None:
    # Guards the strings the partial indexes in the migration rely on.
    assert SubscriptionStatus.ACTIVE.value == "active"
    assert SubscriptionSource.TRIAL.value == "trial"
    assert LicenseStatus.REVOKED.value == "revoked"
