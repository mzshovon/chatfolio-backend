from collections import Counter

from chatfolio.models.profile import JobType
from chatfolio.services.popular_search_service import (
    ProfileFacts,
    normalize_role,
    normalize_skill,
    rank_candidates,
)


def test_skill_aliases_collapse_to_one_topic() -> None:
    assert normalize_skill("ReactJS") == ("react", "React")
    assert normalize_skill("react.js") == ("react", "React")
    assert normalize_skill("Golang") == ("go", "Go")
    assert normalize_skill("Flutter") == ("flutter", "Flutter")


def test_generic_or_unsearchable_skills_are_dropped() -> None:
    assert normalize_skill("Communication") is None
    assert normalize_skill("x") is None
    assert normalize_skill("!!") is None
    assert normalize_skill("one two three four") is None


def test_role_loses_seniority_and_company_noise() -> None:
    assert normalize_role("Sr. Backend Engineer II (Payments) at Acme") == (
        "backend engineer",
        "Backend Engineer",
    )
    assert normalize_role("Senior Product Designer - Acme") == (
        "product designer",
        "Product Designer",
    )
    assert normalize_role("UI/UX Designer") == ("ui/ux designer", "UI/UX Designer")


def test_generic_roles_are_dropped() -> None:
    assert normalize_role("Engineer") is None
    assert normalize_role("Senior Manager") is None
    assert normalize_role("Team Lead") is None


def _profiles(count: int, **kwargs) -> list[ProfileFacts]:
    fields = {k: set(v) if isinstance(v, list) else v for k, v in kwargs.items()}
    return [ProfileFacts(**fields) for _ in range(count)]


DISPLAYS = {
    ("skill", "react"): Counter({"React": 5}),
    ("skill", "go"): Counter({"Go": 4}),
    ("skill", "flutter"): Counter({"Flutter": 2}),
    ("role", "product designer"): Counter({"Product designer": 3}),
}


def test_ranking_orders_by_profile_count_and_applies_min() -> None:
    profiles = (
        _profiles(5, skills=["react"])
        + _profiles(4, skills=["go"])
        + _profiles(2, skills=["flutter"])
        + _profiles(3, roles=["product designer"])
    )
    ranked = rank_candidates(profiles, DISPLAYS, location=None, min_profiles=3)
    assert [c.labels[-1] for c in ranked] == ["React", "Go", "Product designer"]  # Flutter < 3


def test_mode_variant_only_when_it_covers_enough_of_the_topic() -> None:
    mostly_remote = _profiles(4, skills=["react"], mode=JobType.REMOTE) + _profiles(
        1, skills=["react"]
    )
    [cand] = rank_candidates(mostly_remote, DISPLAYS, location=None, min_profiles=3)
    assert cand.labels == ["React remote", "React"]

    scattered = _profiles(1, skills=["react"], mode=JobType.REMOTE) + _profiles(4, skills=["react"])
    [cand] = rank_candidates(scattered, DISPLAYS, location=None, min_profiles=3)
    assert cand.labels == ["React"]


def test_location_boosts_and_localizes() -> None:
    local = _profiles(3, skills=["go"], local=True)
    elsewhere = _profiles(5, skills=["react"])
    ranked = rank_candidates(local + elsewhere, DISPLAYS, location="Dhaka", min_profiles=3)
    # Go: 3 + 3 local = 6 beats React: 5, and gets the localized label first.
    assert [c.labels for c in ranked] == [["Go Dhaka", "Go"], ["React"]]

    # No viewer location: no localized labels, plain popularity ranking.
    ranked = rank_candidates(local + elsewhere, DISPLAYS, location=None, min_profiles=3)
    assert [c.labels for c in ranked] == [["React"], ["Go"]]
