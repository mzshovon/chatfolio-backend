"""Guards the "no bypass via the API" requirement against the most likely failure: someone adds an
endpoint later and forgets to enforce the plan on it.

Every non-admin route must be classified below as one of
  * DEPENDENCY_GATED  - carries `require_feature(...)` (checked here, so it can't be dropped),
  * SERVICE_ENFORCED  - the plan is enforced inside the service it calls (covered by
                        test_enforcement.py / test_quota_service.py),
  * UNGATED           - deliberately independent of the plan.
A new route fails this test until a reviewer files it in one of the three, which is the point.
Admin routes are separately required to sit behind `require_admin`.
"""

from collections.abc import Iterator

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from chatfolio.api.deps import require_admin
from chatfolio.api.v1 import (
    admin,
    admin_plans,
    admin_rbac,
    admin_subscriptions,
    custom_domain,
    meeting_settings,
)
from chatfolio.entitlements.catalog import FeatureKey
from chatfolio.main import app

Op = tuple[str, str]

DEPENDENCY_GATED: dict[Op, FeatureKey] = {
    ("GET", "/api/v1/meeting-settings/google/connect"): FeatureKey.CALENDAR_BOOKING,
    ("POST", "/api/v1/meeting-settings/meetings"): FeatureKey.CALENDAR_BOOKING,
}

SERVICE_ENFORCED: dict[Op, str] = {
    ("POST", "/api/v1/public/chat/{slug}/sessions"): "alias resolution honours links_max",
    ("POST", "/api/v1/public/chat/sessions/{session_id}/messages"): (
        "recruiter_chats_limit, chat_messages_per_session, ai_tokens_limit, model routing"
    ),
    ("POST", "/api/v1/public/chat/sessions/{session_id}/meetings"): (
        "calendar_booking + meetings_limit on the owner"
    ),
    ("GET", "/api/v1/public/chatfolio/{slug}"): "alias resolution, show_badge",
    ("GET", "/api/v1/public/chatfolio/{slug}/cv"): "alias resolution honours links_max",
    ("GET", "/api/v1/dashboard/conversations"): "chat_history_days, recruiter_insights",
    ("GET", "/api/v1/dashboard/conversations/{conversation_id}"): (
        "chat_history_days (by id too), recruiter_insights"
    ),
    ("POST", "/api/v1/dashboard/conversations/{conversation_id}/mark-reviewed"): (
        "chat_history_days (by id too)"
    ),
    ("GET", "/api/v1/dashboard/analytics"): "reports ai_tokens_limit usage",
    ("POST", "/api/v1/portfolio-settings/publish"): "publishes_limit",
    ("GET", "/api/v1/portfolio-settings/aliases"): "links_max (reports in_plan)",
    ("POST", "/api/v1/portfolio-settings/aliases"): "links_max",
    ("PATCH", "/api/v1/portfolio-settings/aliases/{alias_id}"): "links_max (re-activation)",
    ("POST", "/api/v1/portfolio-settings/domain"): "custom_domains_max",
    ("POST", "/api/v1/portfolio-settings/domains"): "custom_domains_max",
    ("POST", "/api/v1/meeting-settings/meetings"): "meetings_limit (+ calendar_booking)",
    ("GET", "/api/v1/sections"): "ai_tokens_limit + model routing",
    ("POST", "/api/v1/sections/{section_id}/regenerate"): "ai_tokens_limit + model routing",
    ("GET", "/api/v1/me/entitlements"): "reports usage",
    ("PUT", "/api/v1/me/models/preference"): "model_selection + plan_models",
}

UNGATED: set[Op] = {
    ("POST", "/api/v1/cv/upload"),  # tokens used by extraction are tallied, never blocked
    ("POST", "/api/v1/cv/{cv_id}/retry"),
    ("GET", "/api/v1/cv/{cv_id}/status"),
    ("GET", "/api/v1/health"),
    ("GET", "/api/v1/me/models"),
    ("GET", "/api/v1/meeting-settings"),  # read-only connection state
    ("DELETE", "/api/v1/meeting-settings/google"),  # disconnecting is always allowed
    ("GET", "/api/v1/portfolio-settings"),
    ("PATCH", "/api/v1/portfolio-settings"),
    ("DELETE", "/api/v1/portfolio-settings/aliases/{alias_id}"),  # freeing a slot is always fine
    ("GET", "/api/v1/portfolio-settings/domain"),
    ("DELETE", "/api/v1/portfolio-settings/domain"),
    ("GET", "/api/v1/portfolio-settings/domains"),
    ("DELETE", "/api/v1/portfolio-settings/domains/{domain_id}"),
    ("POST", "/api/v1/portfolio-settings/unpublish"),  # free, never refunds a publish
    ("GET", "/api/v1/pricing"),
    ("GET", "/api/v1/profiles/me"),
    ("PATCH", "/api/v1/profiles/me"),
    ("GET", "/api/v1/public/chatfolio/search"),
    ("POST", "/api/v1/public/feedback"),
    ("PATCH", "/api/v1/sections/{section_id}"),  # manual edit: no AI involved
    ("POST", "/api/v1/sections/{section_id}/approve"),
    *{
        (method, f"/api/v1/profiles/me/{kind}{suffix}")
        for kind in ("education", "experience", "projects", "skills")
        for method, suffix in (
            ("GET", ""),
            ("POST", ""),
            ("PATCH", "/{item_id}"),
            ("DELETE", "/{item_id}"),
        )
    },
}

# A route may be both dependency-gated and service-enforced (meetings): list it once as gated.
CLASSIFIED = set(DEPENDENCY_GATED) | set(SERVICE_ENFORCED) | UNGATED


def _openapi_ops(prefix: str | None = None, *, exclude: tuple[str, ...] = ()) -> set[Op]:
    ops: set[Op] = set()
    for path, methods in app.openapi()["paths"].items():
        if prefix is not None and not path.startswith(prefix):
            continue
        if any(path.startswith(e) for e in exclude):
            continue
        ops |= {(method.upper(), path) for method in methods}
    return ops


def _walk(dependant: Dependant) -> Iterator[Dependant]:
    for dep in dependant.dependencies:
        yield dep
        yield from _walk(dep)


def _routes(*routers: object) -> dict[Op, APIRoute]:
    found: dict[Op, APIRoute] = {}
    for router in routers:
        for route in router.routes:  # type: ignore[attr-defined]
            if isinstance(route, APIRoute):
                for method in route.methods:
                    found[(method, f"/api/v1{route.path}")] = route
    return found


def test_every_route_is_classified() -> None:
    live = _openapi_ops(exclude=("/api/v1/admin", "/api/v1/auth", "/metrics"))
    unclassified = sorted(live - CLASSIFIED)
    assert not unclassified, (
        "New route(s) need a plan decision - add each to DEPENDENCY_GATED, SERVICE_ENFORCED "
        f"or UNGATED in this file: {unclassified}"
    )


def test_classification_has_no_stale_entries() -> None:
    live = _openapi_ops(exclude=("/api/v1/admin", "/api/v1/auth", "/metrics"))
    assert not sorted(CLASSIFIED - live), "Entries for routes that no longer exist"


def test_dependency_gated_routes_really_carry_their_feature_check() -> None:
    routes = _routes(meeting_settings.router, custom_domain.router)
    for op, feature in DEPENDENCY_GATED.items():
        route = routes[op]
        features = {getattr(d.call, "__feature__", None) for d in _walk(route.dependant)}
        assert feature in features, f"{op} lost its require_feature({feature.value})"


def test_no_route_carries_a_feature_check_that_is_not_declared() -> None:
    routes = _routes(meeting_settings.router, custom_domain.router)
    for op, route in routes.items():
        declared = {getattr(d.call, "__feature__", None) for d in _walk(route.dependant)} - {None}
        assert declared == ({DEPENDENCY_GATED[op]} if op in DEPENDENCY_GATED else set()), op


def test_every_admin_route_requires_an_admin() -> None:
    routes = _routes(
        admin.router, admin_rbac.router, admin_plans.router, admin_subscriptions.router
    )
    live = _openapi_ops("/api/v1/admin")
    assert live <= set(routes), (
        f"Admin routes not covered by this check: {sorted(live - set(routes))}"
    )
    for op in live:
        calls = {d.call for d in _walk(routes[op].dependant)}
        assert require_admin in calls, f"{op} is reachable without admin rights"
