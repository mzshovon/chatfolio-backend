from typing import Any

from sqlalchemy import select

from chatfolio.db.session import get_sessionmaker
from chatfolio.models.user import User, UserRole
from tests.factories.publish_flow import authed_client


async def _promote_to_admin(email: str) -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one()
        user.role = UserRole.ADMIN
        await session.commit()


async def _admin_client(email: str) -> tuple[Any, dict[str, str]]:
    client, headers = await authed_client(email)
    await _promote_to_admin(email)
    return client, headers


async def test_submit_feedback_requires_no_auth() -> None:
    client, _ = await authed_client("feedback-anon-caller@example.com")
    response = await client.post(
        "/api/v1/public/feedback", json={"nps_score": 4, "message": "Loved it"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["nps_score"] == 4
    assert body["message"] == "Loved it"
    assert body["id"]


async def test_submit_feedback_message_is_optional() -> None:
    client, _ = await authed_client("feedback-no-message@example.com")
    response = await client.post("/api/v1/public/feedback", json={"nps_score": 5})
    assert response.status_code == 201
    assert response.json()["message"] is None


async def test_submit_feedback_without_nps_score_returns_422() -> None:
    client, _ = await authed_client("feedback-missing-score@example.com")
    response = await client.post("/api/v1/public/feedback", json={"message": "no score given"})
    assert response.status_code == 422


async def test_submit_feedback_nps_score_above_max_returns_422() -> None:
    client, _ = await authed_client("feedback-score-too-high@example.com")
    response = await client.post("/api/v1/public/feedback", json={"nps_score": 6})
    assert response.status_code == 422


async def test_submit_feedback_nps_score_below_min_returns_422() -> None:
    client, _ = await authed_client("feedback-score-too-low@example.com")
    response = await client.post("/api/v1/public/feedback", json={"nps_score": -1})
    assert response.status_code == 422


async def test_submit_feedback_message_over_max_length_returns_422() -> None:
    client, _ = await authed_client("feedback-message-too-long@example.com")
    response = await client.post(
        "/api/v1/public/feedback", json={"nps_score": 3, "message": "x" * 2101}
    )
    assert response.status_code == 422


async def test_list_feedback_requires_admin() -> None:
    client, headers = await authed_client("feedback-non-admin-caller@example.com")
    response = await client.get("/api/v1/admin/feedback", headers=headers)
    assert response.status_code == 403


async def test_list_feedback_as_admin_returns_submitted_entries() -> None:
    anon_client, _ = await authed_client("feedback-submitter@example.com")
    await anon_client.post(
        "/api/v1/public/feedback", json={"nps_score": 2, "message": "needs work"}
    )

    admin_client, admin_headers = await _admin_client("feedback-admin-reader@example.com")
    response = await admin_client.get("/api/v1/admin/feedback", headers=admin_headers)

    assert response.status_code == 200
    scores = [item["nps_score"] for item in response.json()]
    assert 2 in scores
