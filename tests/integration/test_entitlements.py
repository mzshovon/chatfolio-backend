import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from chatfolio.core.redis import get_redis
from chatfolio.db.session import get_sessionmaker
from chatfolio.entitlements.cache import EntitlementCache
from chatfolio.entitlements.catalog import FEATURES, FeatureKey, ensure_catalog
from chatfolio.entitlements.types import Entitlements
from chatfolio.main import app
from chatfolio.models.billing import (
    EntitlementOverride,
    Feature,
    Plan,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
)
from chatfolio.services.entitlement_service import EntitlementService
from tests.factories.publish_flow import authed_client


def _anon() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _user_id(client: AsyncClient, headers: dict[str, str]) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/v1/auth/me", headers=headers)).json()["id"])


async def _grant(user_id: uuid.UUID, plan_slug: str, *, expires_in: timedelta | None) -> None:
    async with get_sessionmaker()() as session:
        plan = (await session.execute(select(Plan).where(Plan.slug == plan_slug))).scalar_one()
        session.add(
            Subscription(
                user_id=user_id,
                plan_id=plan.id,
                status=SubscriptionStatus.ACTIVE,
                source=SubscriptionSource.ADMIN_GRANT,
                expires_at=datetime.now(UTC) + expires_in if expires_in else None,
            )
        )
        await session.commit()


async def _entitlements(client: AsyncClient, headers: dict[str, str]) -> dict:
    response = await client.get("/api/v1/me/entitlements", headers=headers)
    assert response.status_code == 200
    return response.json()


# ---------- public pricing ----------


async def test_pricing_is_public_and_matches_the_premium_card() -> None:
    response = await _anon().get("/api/v1/pricing")

    assert response.status_code == 200
    plans = response.json()["plans"]
    assert [p["slug"] for p in plans] == ["free", "premium"]

    free, premium = plans
    assert free["is_free"] is True
    assert free["price"]["amount"] == "0"

    assert premium["name"] == "Premium"
    assert premium["price"] == {
        "amount_minor": 50000,
        "amount": "500",
        "currency": "BDT",
        "symbol": "৳",
    }
    assert premium["duration"] == {"unit": "month", "count": 6, "label": "6 months"}
    assert premium["badge_text"] == "MOST POPULAR"
    assert premium["cta_label"] == "Go Premium"
    assert premium["tagline"] == "For active job seekers who want every lead."
    assert premium["bullets"] == [
        "Up to 5 chatfolio links and custom domains",
        "Unlimited recruiter chats and AI answers",
        "Recruiter insights: who asked, what, and from where",
        "Calendar booking for Google Meet and calls",
        "Full chat history, no badge",
    ]


async def test_pricing_bullets_follow_the_real_plan_values() -> None:
    # Edit the plan's actual limit directly and bump the catalogue version (what the admin API
    # will do): the card text must follow, since it is rendered from enforced values.
    async with get_sessionmaker()() as session:
        plan = (await session.execute(select(Plan).where(Plan.slug == "premium"))).scalar_one()
        links = next(f for f in plan.features if f.feature_key == FeatureKey.LINKS_MAX.value)
        links.int_value = 9
        await session.commit()
    await EntitlementCache(get_redis()).bump_catalog_version()

    premium = (await _anon().get("/api/v1/pricing")).json()["plans"][1]
    assert premium["bullets"][0] == "Up to 9 chatfolio links and custom domains"


async def test_pricing_hides_inactive_and_non_public_plans() -> None:
    async with get_sessionmaker()() as session:
        plan = (await session.execute(select(Plan).where(Plan.slug == "premium"))).scalar_one()
        plan.is_public = False
        await session.commit()
    await EntitlementCache(get_redis()).bump_catalog_version()

    plans = (await _anon().get("/api/v1/pricing")).json()["plans"]
    assert [p["slug"] for p in plans] == ["free"]


async def test_pricing_supports_etag_revalidation() -> None:
    client = _anon()
    first = await client.get("/api/v1/pricing")
    etag = first.headers["etag"]

    again = await client.get("/api/v1/pricing", headers={"If-None-Match": etag})

    assert again.status_code == 304
    assert again.content == b""


async def test_unknown_bullet_placeholder_does_not_break_the_page() -> None:
    async with get_sessionmaker()() as session:
        plan = (await session.execute(select(Plan).where(Plan.slug == "free"))).scalar_one()
        plan.marketing_bullets = ["Typo {not_a_feature} here"]
        await session.commit()
    await EntitlementCache(get_redis()).bump_catalog_version()

    response = await _anon().get("/api/v1/pricing")

    assert response.status_code == 200
    assert response.json()["plans"][0]["bullets"] == ["Typo {not_a_feature} here"]


# ---------- /me/entitlements ----------


async def test_my_entitlements_requires_authentication() -> None:
    response = await _anon().get("/api/v1/me/entitlements")
    assert response.status_code == 401


async def test_new_user_gets_the_free_plan() -> None:
    client, headers = await authed_client("ent-free@example.com")

    body = await _entitlements(client, headers)

    assert body["plan"] == {"slug": "free", "name": "Free"}
    assert body["source"] == "free"
    assert body["expires_at"] is None
    assert set(body["features"]) == {f.key.value for f in FEATURES}
    assert body["features"]["recruiter_chats_limit"]["int_value"] == 20
    assert body["features"]["recruiter_chats_limit"]["period"] == "monthly"
    assert body["features"]["recruiter_insights"]["bool_value"] is False
    assert body["features"]["model_selection"]["bool_value"] is False


async def test_premium_subscription_unlocks_premium_values() -> None:
    client, headers = await authed_client("ent-premium@example.com")
    await _grant(await _user_id(client, headers), "premium", expires_in=timedelta(days=180))

    body = await _entitlements(client, headers)

    assert body["plan"]["slug"] == "premium"
    assert body["source"] == "admin_grant"
    assert body["expires_at"] is not None
    assert body["features"]["links_max"]["int_value"] == 5
    assert body["features"]["recruiter_chats_limit"]["unlimited"] is True
    assert body["features"]["recruiter_insights"]["bool_value"] is True


async def test_expired_subscription_falls_back_to_free_even_though_status_is_active() -> None:
    client, headers = await authed_client("ent-expired@example.com")
    user_id = await _user_id(client, headers)
    # status is still 'active' (no cron ran): expiry must be judged from expires_at at read time.
    await _grant(user_id, "premium", expires_in=timedelta(days=-1))

    body = await _entitlements(client, headers)

    assert body["plan"]["slug"] == "free"
    assert body["source"] == "free"


async def test_override_wins_over_plan_value_and_respects_its_own_expiry() -> None:
    client, headers = await authed_client("ent-override@example.com")
    user_id = await _user_id(client, headers)
    async with get_sessionmaker()() as session:
        session.add_all(
            [
                EntitlementOverride(
                    user_id=user_id, feature_key="links_max", int_value=10, reason="support"
                ),
                EntitlementOverride(
                    user_id=user_id,
                    feature_key="custom_domains_max",
                    int_value=3,
                    expires_at=datetime.now(UTC) - timedelta(hours=1),
                    reason="expired promo",
                ),
            ]
        )
        await session.commit()

    features = (await _entitlements(client, headers))["features"]

    assert features["links_max"]["int_value"] == 10
    assert features["custom_domains_max"]["int_value"] == 0  # expired override ignored


async def test_entitlements_are_cached_until_invalidated() -> None:
    client, headers = await authed_client("ent-cache@example.com")
    user_id = await _user_id(client, headers)
    assert (await _entitlements(client, headers))["plan"]["slug"] == "free"

    await _grant(user_id, "premium", expires_in=None)
    # Served from Redis: the DB changed but nothing told the cache.
    assert (await _entitlements(client, headers))["plan"]["slug"] == "free"

    await EntitlementCache(get_redis()).invalidate(user_id)
    assert (await _entitlements(client, headers))["plan"]["slug"] == "premium"


async def test_catalog_version_bump_invalidates_every_users_cache() -> None:
    client, headers = await authed_client("ent-bump@example.com")
    assert (await _entitlements(client, headers))["features"]["links_max"]["int_value"] == 1

    async with get_sessionmaker()() as session:
        plan = (await session.execute(select(Plan).where(Plan.slug == "free"))).scalar_one()
        next(f for f in plan.features if f.feature_key == "links_max").int_value = 2
        await session.commit()
    await EntitlementCache(get_redis()).bump_catalog_version()

    assert (await _entitlements(client, headers))["features"]["links_max"]["int_value"] == 2


async def test_redis_outage_degrades_to_database_not_to_an_error() -> None:
    class BrokenRedis:
        async def get(self, *_: object, **__: object) -> None:
            raise ConnectionError("redis down")

        set = incr = delete = get

    client, headers = await authed_client("ent-nredis@example.com")
    user_id = await _user_id(client, headers)

    async with get_sessionmaker()() as session:
        service = EntitlementService(session, EntitlementCache(BrokenRedis()))  # type: ignore[arg-type]
        entitlements = await service.get(user_id)

    assert entitlements.plan_slug == "free"
    assert entitlements.limit(FeatureKey.LINKS_MAX) == 1


# ---------- value types / catalogue ----------


def test_missing_features_fail_closed() -> None:
    empty = Entitlements("x", "X", "free", None, None, {})
    assert empty.limit(FeatureKey.LINKS_MAX) == 0
    assert empty.enabled(FeatureKey.RECRUITER_INSIGHTS) is False
    assert empty.is_unlimited(FeatureKey.LINKS_MAX) is False


async def test_entitlements_survive_a_json_round_trip() -> None:
    client, headers = await authed_client("ent-json@example.com")
    user_id = await _user_id(client, headers)
    async with get_sessionmaker()() as session:
        original = await EntitlementService(session, EntitlementCache(get_redis())).get(user_id)

    assert Entitlements.from_json(original.to_json()) == original


async def test_ensure_catalog_is_idempotent_and_never_overwrites_admin_edits() -> None:
    async with get_sessionmaker()() as session:
        plan = (await session.execute(select(Plan).where(Plan.slug == "premium"))).scalar_one()
        plan.name = "Pro (renamed by admin)"
        await session.commit()

    async with get_sessionmaker()() as session:
        await ensure_catalog(session)
        await ensure_catalog(session)
        await session.commit()

    async with get_sessionmaker()() as session:
        plans = (await session.execute(select(Plan))).scalars().all()
        features = (await session.execute(select(Feature))).scalars().all()
    assert sorted(p.slug for p in plans) == ["free", "premium"]
    assert {p.name for p in plans} == {"Free", "Pro (renamed by admin)"}
    assert len(features) == len(FEATURES)
