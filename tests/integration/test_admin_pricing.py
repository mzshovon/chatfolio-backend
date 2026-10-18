import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from chatfolio.db.session import get_sessionmaker
from chatfolio.models.audit_log import AdminAuditLog
from chatfolio.models.user import User, UserRole
from tests.factories.publish_flow import authed_client

PREFIX = "/api/v1/admin"


async def _admin(email: str) -> tuple[AsyncClient, dict[str, str]]:
    client, headers = await authed_client(email)
    async with get_sessionmaker()() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        user.role = UserRole.ADMIN
        await session.commit()
    return client, headers


async def _user_id(client: AsyncClient, headers: dict[str, str]) -> str:
    return (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]


async def _plan(client: AsyncClient, headers: dict[str, str], slug: str) -> dict[str, Any]:
    plans = (await client.get(f"{PREFIX}/plans", headers=headers)).json()
    return next(p for p in plans if p["slug"] == slug)


async def _audit_actions() -> list[str]:
    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(select(AdminAuditLog).order_by(AdminAuditLog.created_at))
        ).scalars()
        return [r.action for r in rows]


def _new_plan(slug: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "slug": slug,
        "name": slug.title(),
        "price_minor": 100000,
        "duration_unit": "year",
        "duration_count": 1,
        "cta_label": "Buy",
        "marketing_bullets": ["{links_max} links"],
    }
    body.update(overrides)
    return body


# ---------- access control ----------


async def test_every_admin_pricing_route_rejects_non_admins_and_anonymous() -> None:
    client, headers = await authed_client("ap-candidate@example.com")
    uid = await _user_id(client, headers)
    plan_id = uuid.uuid4()
    calls = [
        ("get", f"{PREFIX}/features"),
        ("get", f"{PREFIX}/plans"),
        ("post", f"{PREFIX}/plans"),
        ("patch", f"{PREFIX}/plans/{plan_id}"),
        ("delete", f"{PREFIX}/plans/{plan_id}"),
        ("put", f"{PREFIX}/plans/{plan_id}/features"),
        ("put", f"{PREFIX}/plans/{plan_id}/models"),
        ("get", f"{PREFIX}/models"),
        ("post", f"{PREFIX}/models"),
        ("patch", f"{PREFIX}/models/{plan_id}"),
        ("delete", f"{PREFIX}/models/{plan_id}"),
        ("get", f"{PREFIX}/users/{uid}/subscription"),
        ("post", f"{PREFIX}/users/{uid}/subscription"),
        ("delete", f"{PREFIX}/users/{uid}/subscription"),
        ("get", f"{PREFIX}/users/{uid}/overrides"),
        ("put", f"{PREFIX}/users/{uid}/overrides/links_max"),
        ("delete", f"{PREFIX}/users/{uid}/overrides/links_max"),
    ]
    for method, url in calls:
        anonymous = await client.request(method.upper(), url)
        forbidden = await client.request(method.upper(), url, headers=headers, json={})
        assert anonymous.status_code == 401, (method, url)
        assert forbidden.status_code == 403, (method, url)


# ---------- features & plans ----------


async def test_list_features_and_plans() -> None:
    client, headers = await _admin("ap-list@example.com")

    features = (await client.get(f"{PREFIX}/features", headers=headers)).json()
    assert len(features) == 12
    assert next(f for f in features if f["key"] == "meetings_limit")["kind"] == "flow"

    premium = await _plan(client, headers, "premium")
    assert premium["features"]["links_max"]["int_value"] == 5
    assert premium["model_ids"] == []  # extra models are opt-in; the default comes from .env
    assert premium["active_subscribers"] == 0


async def test_create_plan_starts_closed_and_shows_on_pricing_page() -> None:
    client, headers = await _admin("ap-create@example.com")

    response = await client.post(f"{PREFIX}/plans", headers=headers, json=_new_plan("yearly"))

    assert response.status_code == 201
    created = response.json()
    assert created["is_default_free"] is False
    assert all(
        (v["int_value"] == 0 or v["bool_value"] is False) for v in created["features"].values()
    )
    pricing = (await client.get("/api/v1/pricing")).json()["plans"]
    yearly = next(p for p in pricing if p["slug"] == "yearly")
    assert yearly["duration"]["label"] == "year"
    assert yearly["bullets"] == ["0 links"]
    assert "plan.create" in await _audit_actions()


async def test_clone_plan_copies_feature_values_and_models() -> None:
    client, headers = await _admin("ap-clone@example.com")
    model = (
        await client.post(
            f"{PREFIX}/models",
            headers=headers,
            json={"provider": "openai", "model_id": "gpt-clone", "display_name": "Clone GPT"},
        )
    ).json()
    source_plan = await _plan(client, headers, "premium")
    await client.put(
        f"{PREFIX}/plans/{source_plan['id']}/models",
        headers=headers,
        json={"model_ids": [model["id"]]},
    )
    source = await _plan(client, headers, "premium")
    assert source["model_ids"] == [model["id"]]

    response = await client.post(
        f"{PREFIX}/plans", headers=headers, json=_new_plan("premium-yearly", clone_from="premium")
    )

    assert response.status_code == 201
    clone = response.json()
    assert clone["features"] == source["features"]
    assert clone["model_ids"] == source["model_ids"]


async def test_create_plan_validation() -> None:
    client, headers = await _admin("ap-create-bad@example.com")
    await client.post(f"{PREFIX}/plans", headers=headers, json=_new_plan("dupe"))

    assert (
        await client.post(f"{PREFIX}/plans", headers=headers, json=_new_plan("dupe"))
    ).status_code == 409
    assert (
        await client.post(f"{PREFIX}/plans", headers=headers, json=_new_plan("Bad Slug"))
    ).status_code == 422
    for bad in (
        {"duration_unit": "lifetime", "duration_count": 3},
        {"duration_unit": "month", "duration_count": 0},
        {"marketing_bullets": ["{nonsense}"]},
        {"marketing_bullets": ["{links_max.__class__}"]},
        {"currency": "taka"},
    ):
        response = await client.post(
            f"{PREFIX}/plans", headers=headers, json=_new_plan("x1", **bad)
        )
        assert response.status_code == 422, bad
    missing_clone = _new_plan("x2", clone_from="nope")
    assert (
        await client.post(f"{PREFIX}/plans", headers=headers, json=missing_clone)
    ).status_code == 404


async def test_update_plan_changes_pricing_page_and_is_audited_with_details() -> None:
    client, headers = await _admin("ap-update@example.com")
    premium = await _plan(client, headers, "premium")

    response = await client.patch(
        f"{PREFIX}/plans/{premium['id']}",
        headers=headers,
        json={"price_minor": 99900, "duration_count": 12, "badge_text": None},
    )

    assert response.status_code == 200
    card = next(
        p for p in (await client.get("/api/v1/pricing")).json()["plans"] if p["slug"] == "premium"
    )
    assert card["price"]["amount"] == "999"
    assert card["duration"]["label"] == "12 months"
    assert card["badge_text"] is None

    async with get_sessionmaker()() as session:
        row = (
            await session.execute(
                select(AdminAuditLog).where(AdminAuditLog.action == "plan.update")
            )
        ).scalar_one()
    assert row.details["changes"]["price_minor"] == {"from": 50000, "to": 99900}


async def test_update_plan_validation_and_free_plan_protection() -> None:
    client, headers = await _admin("ap-update-bad@example.com")
    free = await _plan(client, headers, "free")
    premium = await _plan(client, headers, "premium")

    assert (
        await client.patch(
            f"{PREFIX}/plans/{free['id']}", headers=headers, json={"is_active": False}
        )
    ).status_code == 422
    assert (
        await client.patch(f"{PREFIX}/plans/{premium['id']}", headers=headers, json={"name": None})
    ).status_code == 422
    assert (
        await client.patch(
            f"{PREFIX}/plans/{premium['id']}", headers=headers, json={"duration_unit": "lifetime"}
        )
    ).status_code == 422  # count is still 6
    assert (
        await client.patch(f"{PREFIX}/plans/{uuid.uuid4()}", headers=headers, json={"name": "x"})
    ).status_code == 404


async def test_delete_plan_rules() -> None:
    client, headers = await _admin("ap-delete@example.com")
    free = await _plan(client, headers, "free")
    assert (await client.delete(f"{PREFIX}/plans/{free['id']}", headers=headers)).status_code == 422

    # A plan with subscription history cannot be deleted — only deactivated.
    target, target_headers = await authed_client("ap-delete-target@example.com")
    premium = await _plan(client, headers, "premium")
    await client.post(
        f"{PREFIX}/users/{await _user_id(target, target_headers)}/subscription",
        headers=headers,
        json={"plan_slug": "premium", "reason": "test"},
    )
    assert (
        await client.delete(f"{PREFIX}/plans/{premium['id']}", headers=headers)
    ).status_code == 409

    unused = (await client.post(f"{PREFIX}/plans", headers=headers, json=_new_plan("tmp"))).json()
    assert (
        await client.delete(f"{PREFIX}/plans/{unused['id']}", headers=headers)
    ).status_code == 204
    slugs = [p["slug"] for p in (await client.get(f"{PREFIX}/plans", headers=headers)).json()]
    assert "tmp" not in slugs


# ---------- feature values ----------


async def test_changing_a_plan_limit_reaches_users_immediately_despite_caching() -> None:
    admin, admin_headers = await _admin("ap-feat-admin@example.com")
    user, user_headers = await authed_client("ap-feat-user@example.com")
    free = await _plan(admin, admin_headers, "free")
    before = (await user.get("/api/v1/me/entitlements", headers=user_headers)).json()
    assert before["features"]["links_max"]["int_value"] == 1  # now cached for this user

    response = await admin.put(
        f"{PREFIX}/plans/{free['id']}/features",
        headers=admin_headers,
        json={
            "features": {"links_max": {"int_value": 3}, "recruiter_insights": {"bool_value": True}}
        },
    )

    assert response.status_code == 200
    after = (await user.get("/api/v1/me/entitlements", headers=user_headers)).json()
    assert after["features"]["links_max"]["int_value"] == 3
    assert after["features"]["recruiter_insights"]["bool_value"] is True
    assert "plan.features.update" in await _audit_actions()


async def test_feature_update_validation_and_period_handling() -> None:
    client, headers = await _admin("ap-feat-bad@example.com")
    free = await _plan(client, headers, "free")
    url = f"{PREFIX}/plans/{free['id']}/features"

    for bad in (
        {"nope": {"int_value": 1}},
        {"links_max": {"bool_value": True}},
        {"links_max": {"int_value": -2}},
        {"recruiter_insights": {"int_value": 1}},
        {"links_max": {"int_value": 1, "period": "monthly"}},  # stock feature has no period
    ):
        response = await client.put(url, headers=headers, json={"features": bad})
        assert response.status_code == 422, bad

    # Flow feature: omitting the period keeps the existing one; setting it changes it.
    kept = (
        await client.put(
            url, headers=headers, json={"features": {"recruiter_chats_limit": {"int_value": 50}}}
        )
    ).json()
    assert kept["features"]["recruiter_chats_limit"] == {
        "int_value": 50,
        "bool_value": None,
        "period": "monthly",
    }
    changed = (
        await client.put(
            url,
            headers=headers,
            json={"features": {"recruiter_chats_limit": {"int_value": 5, "period": "daily"}}},
        )
    ).json()
    assert changed["features"]["recruiter_chats_limit"]["period"] == "daily"


# ---------- models ----------


async def test_models_crud_and_the_default_is_read_only_config() -> None:
    client, headers = await _admin("ap-models@example.com")
    assert (await client.get(f"{PREFIX}/models", headers=headers)).json() == []

    created = await client.post(
        f"{PREFIX}/models",
        headers=headers,
        json={"provider": "openai", "model_id": "gpt-test", "display_name": "GPT Test"},
    )
    assert created.status_code == 201
    gpt = created.json()
    assert gpt["is_fallback"] is False
    assert gpt["provider_configured"] is False  # tests run with no API keys at all

    assert (
        await client.post(
            f"{PREFIX}/models",
            headers=headers,
            json={"provider": "openai", "model_id": "gpt-test", "display_name": "dup"},
        )
    ).status_code == 409
    assert (
        await client.post(
            f"{PREFIX}/models",
            headers=headers,
            json={"provider": "claude", "model_id": "x", "display_name": "x"},
        )
    ).status_code == 422

    url = f"{PREFIX}/models/{gpt['id']}"
    updated = await client.patch(
        url, headers=headers, json={"display_name": "GPT Renamed", "is_active": False}
    )
    assert updated.status_code == 200
    assert updated.json()["display_name"] == "GPT Renamed" and updated.json()["is_active"] is False

    # The fallback is configuration (.env), so it can't be set from the API...
    attempt = await client.patch(url, headers=headers, json={"is_fallback": True})
    assert attempt.json()["is_fallback"] is False
    # ...a row only *reports* when it is the same model the env makes the default.
    default_row = (
        await client.post(
            f"{PREFIX}/models",
            headers=headers,
            json={"provider": "deepseek", "model_id": "deepseek-chat", "display_name": "DS"},
        )
    ).json()
    assert default_row["is_fallback"] is True

    assert (await client.delete(url, headers=headers)).status_code == 204
    assert (await client.delete(url, headers=headers)).status_code == 404


async def test_set_plan_models() -> None:
    client, headers = await _admin("ap-plan-models@example.com")
    premium = await _plan(client, headers, "premium")
    gemini = (
        await client.post(
            f"{PREFIX}/models",
            headers=headers,
            json={"provider": "gemini", "model_id": "g-1", "display_name": "Gemini"},
        )
    ).json()
    url = f"{PREFIX}/plans/{premium['id']}/models"

    both = await client.put(
        url, headers=headers, json={"model_ids": [*premium["model_ids"], gemini["id"]]}
    )
    assert both.status_code == 200
    assert gemini["id"] in both.json()["model_ids"]

    assert (
        await client.put(url, headers=headers, json={"model_ids": [str(uuid.uuid4())]})
    ).status_code == 422

    # Deleting a model also removes it from every plan.
    assert (
        await client.delete(f"{PREFIX}/models/{gemini['id']}", headers=headers)
    ).status_code == 204
    assert gemini["id"] not in (await _plan(client, headers, "premium"))["model_ids"]


# ---------- grants & overrides ----------


async def test_grant_unlocks_premium_immediately_and_revoke_returns_to_free() -> None:
    admin, admin_headers = await _admin("ap-grant-admin@example.com")
    user, user_headers = await authed_client("ap-grant-user@example.com")
    uid = await _user_id(user, user_headers)
    assert (await user.get("/api/v1/me/entitlements", headers=user_headers)).json()["plan"][
        "slug"
    ] == "free"  # cached

    granted = await admin.post(
        f"{PREFIX}/users/{uid}/subscription",
        headers=admin_headers,
        json={"plan_slug": "premium", "reason": "launch promo"},
    )

    assert granted.status_code == 201
    current = granted.json()["current"]
    assert current["plan_slug"] == "premium" and current["source"] == "admin_grant"
    expires = datetime.fromisoformat(current["expires_at"])
    assert timedelta(days=178) < expires - datetime.now(UTC) < timedelta(days=186)  # ~6 months
    me = (await user.get("/api/v1/me/entitlements", headers=user_headers)).json()
    assert me["plan"]["slug"] == "premium" and me["source"] == "admin_grant"

    revoked = await admin.delete(f"{PREFIX}/users/{uid}/subscription", headers=admin_headers)
    assert revoked.status_code == 200 and revoked.json()["current"] is None
    assert (await user.get("/api/v1/me/entitlements", headers=user_headers)).json()["plan"][
        "slug"
    ] == "free"
    assert (
        await admin.delete(f"{PREFIX}/users/{uid}/subscription", headers=admin_headers)
    ).status_code == 404
    actions = await _audit_actions()
    assert "subscription.grant" in actions and "subscription.revoke" in actions


async def test_regranting_the_same_plan_extends_from_the_current_end() -> None:
    admin, admin_headers = await _admin("ap-extend-admin@example.com")
    user, user_headers = await authed_client("ap-extend-user@example.com")
    uid = await _user_id(user, user_headers)
    url = f"{PREFIX}/users/{uid}/subscription"
    body = {"plan_slug": "premium", "reason": "x" * 5}

    first = (await admin.post(url, headers=admin_headers, json=body)).json()["current"]
    second = (await admin.post(url, headers=admin_headers, json=body)).json()

    first_end = datetime.fromisoformat(first["expires_at"])
    second_end = datetime.fromisoformat(second["current"]["expires_at"])
    assert timedelta(days=178) < second_end - first_end < timedelta(days=186)
    assert len(second["history"]) == 2  # old one kept, marked cancelled
    assert sorted(h["status"] for h in second["history"]) == ["active", "cancelled"]


async def test_grant_validation() -> None:
    admin, admin_headers = await _admin("ap-grant-bad-admin@example.com")
    user, user_headers = await authed_client("ap-grant-bad-user@example.com")
    url = f"{PREFIX}/users/{await _user_id(user, user_headers)}/subscription"

    async def grant(**body: Any) -> int:
        payload = {"plan_slug": "premium", "reason": "because", **body}
        return (await admin.post(url, headers=admin_headers, json=payload)).status_code

    assert await grant(plan_slug="free") == 422
    assert await grant(plan_slug="ghost") == 404
    assert await grant(expires_at=(datetime.now(UTC) - timedelta(days=1)).isoformat()) == 422
    assert await grant(reason="") == 422
    missing_user = f"{PREFIX}/users/{uuid.uuid4()}/subscription"
    assert (
        await admin.post(
            missing_user, headers=admin_headers, json={"plan_slug": "premium", "reason": "abc"}
        )
    ).status_code == 404


async def test_explicit_expiry_is_honoured() -> None:
    admin, admin_headers = await _admin("ap-expiry-admin@example.com")
    user, user_headers = await authed_client("ap-expiry-user@example.com")
    uid = await _user_id(user, user_headers)
    target = datetime.now(UTC) + timedelta(days=7)

    response = await admin.post(
        f"{PREFIX}/users/{uid}/subscription",
        headers=admin_headers,
        json={"plan_slug": "premium", "reason": "7 day trial", "expires_at": target.isoformat()},
    )

    assert response.status_code == 201
    got = datetime.fromisoformat(response.json()["current"]["expires_at"])
    assert abs((got - target).total_seconds()) < 1


async def test_overrides_apply_list_and_delete() -> None:
    admin, admin_headers = await _admin("ap-ovr-admin@example.com")
    user, user_headers = await authed_client("ap-ovr-user@example.com")
    uid = await _user_id(user, user_headers)
    url = f"{PREFIX}/users/{uid}/overrides/links_max"
    assert (await user.get("/api/v1/me/entitlements", headers=user_headers)).json()["features"][
        "links_max"
    ]["int_value"] == 1  # cached

    put = await admin.put(url, headers=admin_headers, json={"int_value": 10, "reason": "VIP"})

    assert put.status_code == 200
    assert put.json()["granted_by"] is not None
    me = (await user.get("/api/v1/me/entitlements", headers=user_headers)).json()
    assert me["features"]["links_max"]["int_value"] == 10
    listed = (await admin.get(f"{PREFIX}/users/{uid}/overrides", headers=admin_headers)).json()
    assert [o["feature_key"] for o in listed] == ["links_max"]

    assert (await admin.delete(url, headers=admin_headers)).status_code == 204
    me = (await user.get("/api/v1/me/entitlements", headers=user_headers)).json()
    assert me["features"]["links_max"]["int_value"] == 1
    assert (await admin.delete(url, headers=admin_headers)).status_code == 404


async def test_override_validation() -> None:
    admin, admin_headers = await _admin("ap-ovr-bad-admin@example.com")
    user, user_headers = await authed_client("ap-ovr-bad-user@example.com")
    base = f"{PREFIX}/users/{await _user_id(user, user_headers)}/overrides"

    async def put(feature: str, **body: Any) -> int:
        payload = {"reason": "because", **body}
        return (
            await admin.put(f"{base}/{feature}", headers=admin_headers, json=payload)
        ).status_code

    assert await put("ghost", int_value=1) == 404
    assert await put("links_max", bool_value=True) == 422
    assert await put("links_max", int_value=-5) == 422
    assert await put("recruiter_insights", int_value=1) == 422
    assert await put("links_max", int_value=2, reason="") == 422
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    assert await put("links_max", int_value=2, expires_at=past) == 422
