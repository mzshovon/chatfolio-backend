import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.config.settings import PopularSearchSettings
from chatfolio.models.chatfolio import PublicChatfolio
from chatfolio.models.profile import CandidateProfile, Experience, JobType, Skill
from chatfolio.schemas.public_portfolio import PopularSearchItem
from chatfolio.services.portfolio_service import PublicPortfolioService

logger = structlog.get_logger(__name__)

_CACHE_PREFIX = "popular_search:"

MAX_PILLS = 5
# Of the five pills, at most this many may be skills (or roles) while the other kind still has
# qualifying candidates — keeps the row varied. Relaxed if only one kind qualifies.
MAX_PER_KIND = 3
# A "React remote" / "Data analyst Dhaka" variant replaces the bare topic only when it still
# covers this share of it; otherwise the narrower pill would be a weaker shortcut than the topic.
SPECIFIC_RATIO = 0.4
# Most candidates whose real search count gets verified per cache miss.
CANDIDATE_POOL = 20
MAX_LABEL_WORDS = 4

SKILL_ALIASES = {
    "js": "javascript",
    "ts": "typescript",
    "py": "python",
    "reactjs": "react",
    "react.js": "react",
    "nodejs": "node",
    "node.js": "node",
    "vuejs": "vue",
    "vue.js": "vue",
    "golang": "go",
    "postgres": "postgresql",
    "k8s": "kubernetes",
}
# Display text for a skill that was reached through an alias ("reactjs" -> "React"). The targets
# are the shortest common stem, so searching them still matches the longer spellings.
ALIAS_DISPLAY = {
    "javascript": "JavaScript", "typescript": "TypeScript", "python": "Python", "react": "React",
    "node": "Node", "vue": "Vue", "go": "Go", "postgresql": "PostgreSQL",
    "kubernetes": "Kubernetes",
}  # fmt: skip
# Too generic to be worth searching for.
GENERIC_SKILLS = frozenset(
    {
        "communication", "teamwork", "leadership", "problem solving", "time management",
        "management", "microsoft office", "ms office", "office", "english", "internet",
        "windows", "computer", "hardworking", "creativity", "adaptability",
    }
)  # fmt: skip
SENIORITY = frozenset(
    {
        "senior", "sr", "junior", "jr", "lead", "principal", "staff", "intern", "trainee",
        "associate", "i", "ii", "iii", "iv", "v",
    }
)  # fmt: skip
GENERIC_ROLES = frozenset(
    {
        "engineer", "developer", "manager", "consultant", "specialist", "officer", "executive",
        "assistant", "member", "employee", "worker", "freelancer", "team",
    }
)  # fmt: skip

_ROLE_CUT = re.compile(r"\(|\)|,|\||@|\s[-–—]\s|\sat\s", re.IGNORECASE)


def normalize_skill(name: str) -> tuple[str, str] | None:
    """(grouping key, display text) for a skill, or None if it isn't a useful search term."""
    display = " ".join(name.split())
    key = display.lower()
    if not 2 <= len(key) <= 30 or len(key.split()) > 3 or not re.search(r"[a-z0-9]", key):
        return None
    if key in GENERIC_SKILLS:
        return None
    if key in SKILL_ALIASES:
        key = SKILL_ALIASES[key]
        display = ALIAS_DISPLAY[key]
    return key, display


def normalize_role(role: str) -> tuple[str, str] | None:
    """(grouping key, display text) for a job title with seniority and company noise removed:
    "Sr. Backend Engineer II (Payments) at Acme" -> "Backend Engineer"."""
    head = _ROLE_CUT.split(role, maxsplit=1)[0]
    words = [w for w in head.split() if w.strip(".").lower() not in SENIORITY and not w.isdigit()]
    if not 1 <= len(words) <= 3:
        return None
    display = " ".join(words)
    key = display.lower()
    if key in GENERIC_ROLES or len(key) < 3:
        return None
    return key, display[0].upper() + display[1:] if display.islower() else display


@dataclass
class ProfileFacts:
    """What one published profile contributes: normalized skill/role keys, its work mode, and
    whether it sits in the viewer's location."""

    skills: set[str] = field(default_factory=set)
    roles: set[str] = field(default_factory=set)
    mode: JobType | None = None
    local: bool = False


@dataclass
class Candidate:
    kind: str  # "skill" | "role"
    score: int
    # Preferred pill text first, the bare topic last; the first that verifies is used.
    labels: list[str]


def _best_display(counter: Counter[str]) -> str:
    # Most common spelling; ties go to the shortest, then alphabetical, so output is stable.
    return min(counter, key=lambda d: (-counter[d], len(d), d))


def rank_candidates(
    profiles: list[ProfileFacts],
    displays: dict[tuple[str, str], Counter[str]],
    *,
    location: str | None,
    min_profiles: int,
) -> list[Candidate]:
    """Pure ranking: topics (a skill or role) by how many distinct published profiles have them,
    plus how many of those are local when the viewer's location is known. Each topic yields at
    most one pill — the most specific variant that still covers enough of the topic."""
    plain: Counter[tuple[str, str]] = Counter()
    local: Counter[tuple[str, str]] = Counter()
    by_mode: Counter[tuple[str, str, JobType]] = Counter()
    for p in profiles:
        topics = [("skill", k) for k in p.skills] + [("role", k) for k in p.roles]
        for topic in topics:
            plain[topic] += 1
            if p.local and location:
                local[topic] += 1
            if p.mode:
                by_mode[(*topic, p.mode)] += 1

    candidates: list[Candidate] = []
    for topic, n in plain.items():
        if n < min_profiles:
            continue
        text = _best_display(displays[topic])
        bar = max(min_profiles, SPECIFIC_RATIO * n)
        labels: list[str] = []
        if location and local[topic] >= bar:
            labels.append(f"{text} {location}")
        modes = [(by_mode[(*topic, m)], m) for m in JobType if by_mode[(*topic, m)] >= bar]
        if modes:
            # Most common mode; ties resolved by enum order so the choice is deterministic.
            labels.append(f"{text} {max(modes, key=lambda cm: cm[0])[1].value}")
        labels = [lb for lb in labels if len(lb.split()) <= MAX_LABEL_WORDS]
        labels.append(text)
        candidates.append(Candidate(kind=topic[0], score=n + local[topic], labels=labels))
    candidates.sort(key=lambda c: (-c.score, c.labels[-1].lower()))
    return candidates


class PopularSearchService:
    """Suggests the pills under the recruiter search box. Derived entirely from published
    Chatfolios' CV data (skills, experience roles, work mode, location); every pill is a plain
    `q` for the existing search and is verified against that same search, so it never leads to
    an empty page and its count is exactly what clicking it returns."""

    def __init__(
        self,
        session: AsyncSession,
        portfolio: PublicPortfolioService,
        redis: Redis,
        settings: PopularSearchSettings,
    ) -> None:
        self._session = session
        self._portfolio = portfolio
        self._redis = redis
        self._settings = settings

    async def popular(
        self, *, limit: int = MAX_PILLS, location: str | None = None
    ) -> list[PopularSearchItem]:
        location = " ".join(location.split()) if location else None
        limit = min(limit, MAX_PILLS)
        cache_key = f"{_CACHE_PREFIX}{limit}:{location.casefold() if location else '-'}"
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        profiles, displays = await self._load(location)
        candidates = rank_candidates(
            profiles, displays, location=location, min_profiles=self._settings.min_profiles
        )
        items = await self._verify(candidates[:CANDIDATE_POOL], limit)
        # Most results first, so the strongest shortcut leads the row.
        items.sort(key=lambda i: (-i.count, i.label.lower()))
        await self._cache_set(cache_key, items)
        return items

    async def _verify(self, pool: list[Candidate], limit: int) -> list[PopularSearchItem]:
        chosen: list[PopularSearchItem] = []
        taken: set[int] = set()
        seen_labels: set[str] = set()
        per_kind: Counter[str] = Counter()
        for enforce_cap in (True, False):
            for index, cand in enumerate(pool):
                if len(chosen) >= limit:
                    return chosen
                if index in taken or (enforce_cap and per_kind[cand.kind] >= MAX_PER_KIND):
                    continue
                for label in cand.labels:
                    if label.lower() in seen_labels:
                        continue
                    count = await self._portfolio.count_published(label)
                    if count >= self._settings.min_profiles:
                        chosen.append(PopularSearchItem(label=label, q=label, count=count))
                        seen_labels.add(label.lower())
                        per_kind[cand.kind] += 1
                        taken.add(index)
                        break
        return chosen

    async def _load(
        self, location: str | None
    ) -> tuple[list[ProfileFacts], dict[tuple[str, str], Counter[str]]]:
        published = select(PublicChatfolio.profile_id).where(PublicChatfolio.is_published.is_(True))
        facts: dict[uuid.UUID, ProfileFacts] = {}
        displays: dict[tuple[str, str], Counter[str]] = {}
        needle = location.casefold() if location else None

        rows = await self._session.execute(
            select(
                CandidateProfile.id,
                CandidateProfile.title,
                CandidateProfile.location,
                CandidateProfile.job_type,
            ).where(CandidateProfile.id.in_(published))
        )
        titles: dict[uuid.UUID, str] = {}
        for pid, title, loc, job_type in rows:
            facts[pid] = ProfileFacts(
                mode=job_type, local=bool(needle and loc and needle in loc.casefold())
            )
            if title:
                titles[pid] = title

        def add(pid: uuid.UUID, kind: str, normalized: tuple[str, str] | None) -> None:
            if normalized is None:
                return
            key, display = normalized
            (facts[pid].skills if kind == "skill" else facts[pid].roles).add(key)
            displays.setdefault((kind, key), Counter())[display] += 1

        for pid, name in await self._session.execute(
            select(Skill.profile_id, Skill.name).where(Skill.profile_id.in_(published))
        ):
            add(pid, "skill", normalize_skill(name))
        role_rows = await self._session.execute(
            select(Experience.profile_id, Experience.role).where(
                Experience.profile_id.in_(published)
            )
        )
        for pid, role in role_rows:
            add(pid, "role", normalize_role(role))
        for pid, title in titles.items():
            add(pid, "role", normalize_role(title))
        return list(facts.values()), displays

    async def _cache_get(self, key: str) -> list[PopularSearchItem] | None:
        if self._settings.cache_ttl_seconds <= 0:
            return None
        try:
            raw = await self._redis.get(key)
            return [PopularSearchItem(**i) for i in json.loads(raw)] if raw else None
        except Exception:
            logger.warning("popular_search.cache_read_failed", exc_info=True)
            return None

    async def _cache_set(self, key: str, items: list[PopularSearchItem]) -> None:
        if self._settings.cache_ttl_seconds <= 0:
            return
        try:
            payload = json.dumps([i.model_dump() for i in items])
            await self._redis.set(key, payload, ex=self._settings.cache_ttl_seconds)
        except Exception:
            logger.warning("popular_search.cache_write_failed", exc_info=True)
