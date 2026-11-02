from collections.abc import Callable, Iterator

import pytest

from chatfolio.api.deps import get_llm_provider_factory
from chatfolio.config.settings import get_settings
from chatfolio.core.redis import get_redis
from chatfolio.main import app
from tests.factories.fake_llm import FakeLLMFactory
from tests.factories.publish_flow import authed_client, publish_full_profile

POPULAR = "/api/v1/public/chatfolio/popular"


@pytest.fixture
def set_fake_llm() -> Iterator[Callable[[str], None]]:
    def _set(response: str) -> None:
        app.dependency_overrides[get_llm_provider_factory] = lambda: FakeLLMFactory(response)

    yield _set
    app.dependency_overrides.pop(get_llm_provider_factory, None)


@pytest.fixture(autouse=True)
def no_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings().popular_search, "cache_ttl_seconds", 0)


async def _publish(
    n: int, skills: list[str], set_fake_llm: Callable[[str], None], *, location: str = "Dhaka"
):
    client, headers, _slug = await publish_full_profile(
        f"popular-{n}@example.com", set_fake_llm, f"popular-{n}"
    )
    await client.patch(
        "/api/v1/profiles/me", headers=headers, json={"location": location, "job_type": "remote"}
    )
    for name in skills:
        await client.post("/api/v1/profiles/me/skills", headers=headers, json={"name": name})
    return client


async def _seed(set_fake_llm: Callable[[str], None]):
    # 4 Backend Engineers in Dhaka (remote): all React, 3 Python, 2 Flutter (below the minimum).
    skills = [["React", "Python", "Flutter"]] * 2 + [["ReactJS", "Python"], ["React"]]
    client = None
    for n, s in enumerate(skills):
        client = await _publish(n, s, set_fake_llm)
    return client


async def test_empty_when_nothing_qualifies() -> None:
    client, _ = await authed_client("popular-empty@example.com")
    response = await client.get(POPULAR)
    assert response.status_code == 200
    assert response.json() == []


async def test_pills_are_ranked_by_count_and_searchable(
    set_fake_llm: Callable[[str], None],
) -> None:
    client = await _seed(set_fake_llm)

    response = await client.get(POPULAR)

    assert response.status_code == 200
    assert response.json() == [
        {"label": "Backend Engineer remote", "q": "Backend Engineer remote", "count": 4},
        {"label": "React remote", "q": "React remote", "count": 4},
        {"label": "Python remote", "q": "Python remote", "count": 3},
    ]  # Flutter has only 2 published profiles; ReactJS was folded into React
    # Every pill is a ready-made search whose result size is exactly the advertised count.
    for pill in response.json():
        found = await client.get("/api/v1/public/chatfolio/search", params={"q": pill["q"]})
        assert len(found.json()) == pill["count"]


async def test_location_localizes_pills(set_fake_llm: Callable[[str], None]) -> None:
    client = await _seed(set_fake_llm)

    labels = [p["label"] for p in (await client.get(POPULAR, params={"location": "Dhaka"})).json()]
    assert labels == ["Backend Engineer Dhaka", "React Dhaka", "Python Dhaka"]

    # A city with nobody in it falls back to the global pills.
    labels = [p["label"] for p in (await client.get(POPULAR, params={"location": "Oslo"})).json()]
    assert labels == ["Backend Engineer remote", "React remote", "Python remote"]


async def test_limit_is_capped_and_validated(set_fake_llm: Callable[[str], None]) -> None:
    client = await _seed(set_fake_llm)

    assert len((await client.get(POPULAR, params={"limit": 2})).json()) == 2
    assert (await client.get(POPULAR, params={"limit": 6})).status_code == 422
    assert (await client.get(POPULAR, params={"location": "x"})).status_code == 422


async def test_unpublished_profiles_never_count(set_fake_llm: Callable[[str], None]) -> None:
    client = await _seed(set_fake_llm)
    # A registered-but-unpublished candidate would lift Flutter from 2 to the minimum of 3.
    draft, headers = await authed_client("popular-draft@example.com")
    await draft.post("/api/v1/profiles/me/skills", headers=headers, json={"name": "Flutter"})

    labels = [p["label"] for p in (await client.get(POPULAR)).json()]
    assert not any("Flutter" in label for label in labels)


async def test_result_is_cached(
    set_fake_llm: Callable[[str], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings().popular_search, "cache_ttl_seconds", 60)
    redis = get_redis()
    async for key in redis.scan_iter("popular_search:*"):
        await redis.delete(key)
    client = await _seed(set_fake_llm)

    first = (await client.get(POPULAR)).json()
    await _publish(50, ["Rust"], set_fake_llm)  # not visible until the cache entry expires
    assert (await client.get(POPULAR)).json() == first
    assert await redis.ttl("popular_search:5:-") > 0
