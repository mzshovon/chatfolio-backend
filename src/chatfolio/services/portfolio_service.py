import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Literal, TypeVar
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.core.exceptions import ConflictError, NotFoundError, ValidationFailedError
from chatfolio.entitlements.catalog import FeatureKey
from chatfolio.models.chat import ChatSession, RecruiterMetadata
from chatfolio.models.chatfolio import ChatfolioLink, PortfolioVisit, PublicChatfolio
from chatfolio.models.cv import CVStatus, UploadedCV
from chatfolio.models.mixins import ProfileChildMixin
from chatfolio.models.portfolio_section import PortfolioSection, SectionStatus, SectionType
from chatfolio.models.profile import (
    CandidateProfile,
    Experience,
    JobType,
    ProfileStatus,
    Skill,
)
from chatfolio.models.user import User
from chatfolio.repositories.profile_repository import ProfileRepository
from chatfolio.schemas.portfolio_settings import PortfolioSettingsUpdateRequest
from chatfolio.services.chatfolio_resolver import ChatfolioResolver
from chatfolio.services.entitlement_guard import EntitlementGuard
from chatfolio.services.profile_service import ProfileService

SUBDOMAIN_SUFFIX = ".chatfolio.chat"

ChildT = TypeVar("ChildT", bound=ProfileChildMixin)


HEADLINE_MAX = 140
TOP_SKILLS_MAX = 5
SearchStart = Literal["immediately", "30"]
SearchExp = Literal["0-2", "3-5", "6+"]


@dataclass
class SearchHit:
    chatfolio: PublicChatfolio
    profile: CandidateProfile
    headline: str | None = None
    current_company: str | None = None
    years_experience: float | None = None
    top_skills: list[str] = field(default_factory=list)


def _make_headline(text: str | None) -> str | None:
    if not text:
        return None
    flat = " ".join(text.split())
    if not flat:
        return None
    if len(flat) <= HEADLINE_MAX:
        return flat
    return flat[: HEADLINE_MAX - 1].rstrip() + "…"


def _window_minutes(hours: dict[str, str], day: date) -> tuple[float, float] | None:
    """Candidate's working window on `day` as absolute UTC minute offsets (handles overnight)."""
    try:
        tz = ZoneInfo(hours["timezone"])
        sh, sm = map(int, hours["start"].split(":"))
        eh, em = map(int, hours["end"].split(":"))
    except (KeyError, ValueError, Exception):
        return None
    start = datetime(day.year, day.month, day.day, sh, sm, tzinfo=tz)
    end = datetime(day.year, day.month, day.day, eh, em, tzinfo=tz)
    if end <= start:
        end += timedelta(days=1)
    return start.timestamp() / 60, end.timestamp() / 60


def hours_overlap(a: dict[str, str] | None, b: dict[str, str] | None) -> bool:
    if not a or not b:
        return False
    today = datetime.now(UTC).date()
    # Compare across adjacent days so overnight windows in far timezones still meet.
    for da in (-1, 0, 1):
        wa = _window_minutes(a, today + timedelta(days=da))
        wb = _window_minutes(b, today)
        if wa and wb and min(wa[1], wb[1]) > max(wa[0], wb[0]):
            return True
    return False


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "candidate"


def subdomain_for(slug: str) -> str:
    return f"{slug}{SUBDOMAIN_SUFFIX}"


class PortfolioService:
    """Owner-facing: portfolio settings, slug management, publish/unpublish gating."""

    def __init__(
        self, session: AsyncSession, profile_service: ProfileService, guard: EntitlementGuard
    ) -> None:
        self._session = session
        self._profile_service = profile_service
        self._guard = guard

    async def get_or_create_for_user(self, user: User) -> PublicChatfolio:
        profile = await self._profile_service.get_or_create_for_user(user)
        result = await self._session.execute(
            select(PublicChatfolio).where(PublicChatfolio.profile_id == profile.id)
        )
        chatfolio = result.scalar_one_or_none()
        if chatfolio is None:
            profile_id = profile.id
            slug = await self._generate_unique_slug(profile.full_name or "candidate")
            try:
                # A SAVEPOINT confines the rollback to this insert on conflict, instead of
                # expiring every object already loaded in the outer session (e.g. `profile`).
                async with self._session.begin_nested():
                    chatfolio = PublicChatfolio(profile_id=profile_id, slug=slug)
                    self._session.add(chatfolio)
                    await self._session.flush()
            except IntegrityError:
                # Lost a race against a concurrent request creating the same chatfolio
                # (e.g. parallel dashboard calls right after registration).
                result = await self._session.execute(
                    select(PublicChatfolio).where(PublicChatfolio.profile_id == profile_id)
                )
                chatfolio = result.scalar_one_or_none()
                if chatfolio is None:
                    raise
        return chatfolio

    async def update_settings(
        self, user: User, payload: PortfolioSettingsUpdateRequest
    ) -> PublicChatfolio:
        chatfolio = await self.get_or_create_for_user(user)
        data = payload.model_dump(exclude_unset=True)

        new_slug = data.pop("slug", None)
        renamed = new_slug is not None and new_slug != chatfolio.slug
        if renamed:
            await self._ensure_slug_available(new_slug, exclude_id=chatfolio.id)
            # Every rename of the public slug spends one unit of the plan's `publishes_limit`
            # (like a publish), so the cap can't be sidestepped by renaming a live page.
            await self._guard.consume(user.id, FeatureKey.PUBLISHES_LIMIT)

        try:
            if renamed:
                chatfolio.previous_slug = chatfolio.slug
                chatfolio.slug = new_slug
            for field, value in data.items():
                setattr(chatfolio, field, value)
            await self._session.flush()
        except BaseException:
            if renamed:
                await self._guard.release(user.id, FeatureKey.PUBLISHES_LIMIT)
            raise
        return chatfolio

    async def publish(self, user: User) -> PublicChatfolio:
        profile = await self._profile_service.get_or_create_for_user(user)
        sections = await self._profile_service.list_children(user, PortfolioSection)
        approved_types = {s.section_type for s in sections if s.status == SectionStatus.APPROVED}

        errors: list[str] = []
        missing = [t.value for t in SectionType if t not in approved_types]
        if missing:
            errors.append(f"Approve these sections before publishing: {', '.join(missing)}.")
        if not profile.full_name:
            errors.append("Add your full name before publishing.")
        if errors:
            raise ValidationFailedError(" ".join(errors))

        chatfolio = await self.get_or_create_for_user(user)
        # Each unpublished -> published transition is one publish action against the plan's
        # `publishes_limit` (re-publishing a live page changes nothing, so it is free; so is
        # unpublishing). Reserved last, once every validation has passed.
        newly_published = not chatfolio.is_published
        if newly_published:
            await self._guard.consume(user.id, FeatureKey.PUBLISHES_LIMIT)
        try:
            profile.status = ProfileStatus.APPROVED
            chatfolio.is_published = True
            chatfolio.published_at = datetime.now(UTC)
            await self._session.flush()
        except BaseException:
            if newly_published:
                await self._guard.release(user.id, FeatureKey.PUBLISHES_LIMIT)
            raise
        return chatfolio

    async def unpublish(self, user: User) -> PublicChatfolio:
        chatfolio = await self.get_or_create_for_user(user)
        chatfolio.is_published = False
        await self._session.flush()
        return chatfolio

    async def _generate_unique_slug(self, base: str) -> str:
        base_slug = slugify(base)
        for _ in range(5):
            candidate = f"{base_slug}-{secrets.token_hex(3)}"
            if not await self._slug_exists(candidate):
                return candidate
        raise RuntimeError("Could not generate a unique slug after several attempts.")

    async def _slug_exists(self, slug: str) -> bool:
        result = await self._session.execute(
            select(PublicChatfolio.id).where(PublicChatfolio.slug == slug)
        )
        if result.scalar_one_or_none() is not None:
            return True
        alias = await self._session.execute(
            select(ChatfolioLink.id).where(ChatfolioLink.slug == slug)
        )
        return alias.scalar_one_or_none() is not None

    async def _ensure_slug_available(self, slug: str, *, exclude_id: uuid.UUID) -> None:
        result = await self._session.execute(
            select(PublicChatfolio.id).where(
                PublicChatfolio.slug == slug, PublicChatfolio.id != exclude_id
            )
        )
        taken = (
            result.scalar_one_or_none() is not None
            or (
                await self._session.execute(
                    select(ChatfolioLink.id).where(ChatfolioLink.slug == slug)
                )
            ).scalar_one_or_none()
            is not None
        )
        if taken:
            raise ConflictError(f"Slug {slug!r} is already taken.")

    # ---- aliases (extra links of the same chatfolio) ----

    async def list_aliases(self, user: User) -> list[tuple[ChatfolioLink, bool]]:
        """Each alias with whether the owner's *current* plan still honours it."""
        chatfolio = await self.get_or_create_for_user(user)
        entitlements = await self._guard.get(user.id)
        links = await self._aliases(chatfolio.id)
        allowed = (
            len(links)
            if entitlements.is_unlimited(FeatureKey.LINKS_MAX)
            else max(entitlements.limit(FeatureKey.LINKS_MAX) - 1, 0)
        )
        honoured = {link.id for link in [lk for lk in links if lk.is_active][:allowed]}
        return [(link, link.id in honoured) for link in links]

    async def add_alias(self, user: User, slug: str) -> ChatfolioLink:
        chatfolio = await self.get_or_create_for_user(user)
        await self._guard.lock_stock(user.id, FeatureKey.LINKS_MAX)
        await self._guard.lock_and_check_room(
            user.id, FeatureKey.LINKS_MAX, await self._link_count(chatfolio.id)
        )
        await self._ensure_alias_slug_available(slug)
        link = ChatfolioLink(chatfolio_id=chatfolio.id, slug=slug)
        self._session.add(link)
        await self._session.flush()
        return link

    async def update_alias(
        self, user: User, alias_id: uuid.UUID, *, slug: str | None, is_active: bool | None
    ) -> ChatfolioLink:
        chatfolio = await self.get_or_create_for_user(user)
        link = await self._owned_alias(chatfolio.id, alias_id)
        if slug is not None and slug != link.slug:
            await self._ensure_alias_slug_available(slug, exclude_alias_id=link.id)
            link.slug = slug
        if is_active is True and not link.is_active:
            await self._guard.lock_stock(user.id, FeatureKey.LINKS_MAX)
            await self._guard.lock_and_check_room(
                user.id, FeatureKey.LINKS_MAX, await self._link_count(chatfolio.id)
            )
            link.is_active = True
        elif is_active is False:
            link.is_active = False
        await self._session.flush()
        return link

    async def delete_alias(self, user: User, alias_id: uuid.UUID) -> None:
        chatfolio = await self.get_or_create_for_user(user)
        link = await self._owned_alias(chatfolio.id, alias_id)
        await self._session.delete(link)
        await self._session.flush()

    async def _aliases(self, chatfolio_id: uuid.UUID) -> list[ChatfolioLink]:
        result = await self._session.execute(
            select(ChatfolioLink)
            .where(ChatfolioLink.chatfolio_id == chatfolio_id)
            .order_by(ChatfolioLink.created_at, ChatfolioLink.id)
        )
        return list(result.scalars())

    async def _link_count(self, chatfolio_id: uuid.UUID) -> int:
        """Links held right now: the primary slug plus every active alias."""
        active = (
            await self._session.execute(
                select(func.count())
                .select_from(ChatfolioLink)
                .where(
                    ChatfolioLink.chatfolio_id == chatfolio_id, ChatfolioLink.is_active.is_(True)
                )
            )
        ).scalar_one()
        return 1 + int(active)

    async def _owned_alias(self, chatfolio_id: uuid.UUID, alias_id: uuid.UUID) -> ChatfolioLink:
        link = await self._session.get(ChatfolioLink, alias_id)
        if link is None or link.chatfolio_id != chatfolio_id:
            raise NotFoundError("Alias not found.")
        return link

    async def _ensure_alias_slug_available(
        self, slug: str, *, exclude_alias_id: uuid.UUID | None = None
    ) -> None:
        in_primary = (
            await self._session.execute(
                select(PublicChatfolio.id).where(PublicChatfolio.slug == slug)
            )
        ).scalar_one_or_none()
        in_alias = (
            await self._session.execute(
                select(ChatfolioLink.id).where(
                    ChatfolioLink.slug == slug,
                    ChatfolioLink.id != (exclude_alias_id or uuid.UUID(int=0)),
                )
            )
        ).scalar_one_or_none()
        if in_primary is not None or in_alias is not None:
            raise ConflictError(f"Slug {slug!r} is already taken.")


class PublicPortfolioService:
    """Public, unauthenticated: resolves a Chatfolio by slug. Never reads draft/unapproved data —
    only `is_published` chatfolios, and only `approved` PortfolioSection content, are exposed."""

    def __init__(
        self, session: AsyncSession, profile_repository: ProfileRepository, guard: EntitlementGuard
    ) -> None:
        self._session = session
        self._profile_repository = profile_repository
        self._guard = guard
        self._resolver = ChatfolioResolver(session, guard)

    async def get_published_by_slug(self, slug: str) -> PublicChatfolio | None:
        """Primary slug or an active alias still inside the owner's plan."""
        return await self._resolver.resolve_published(slug)

    async def show_badge(self, owner_id: uuid.UUID) -> bool:
        """The "made with Chatfolio" badge is shown unless the owner's plan removes it."""
        return not (await self._guard.get(owner_id)).enabled(FeatureKey.REMOVE_BADGE)

    async def find_redirect_target(self, old_slug: str) -> str | None:
        result = await self._session.execute(
            select(PublicChatfolio.slug).where(
                PublicChatfolio.previous_slug == old_slug, PublicChatfolio.is_published.is_(True)
            )
        )
        return result.scalar_one_or_none()

    async def get_profile(self, profile_id: uuid.UUID) -> CandidateProfile | None:
        return await self._session.get(CandidateProfile, profile_id)

    async def search_published(
        self,
        *,
        q: str | None = None,
        username: str | None = None,
        location: str | None = None,
        job_type: JobType | None = None,
        field: str | None = None,
        start: SearchStart | None = None,
        exp: SearchExp | None = None,
        overlap_hours: dict[str, str] | None = None,
        limit: int = 25,
    ) -> list[SearchHit]:
        """Recruiter-facing search across published Chatfolios. Every filter is optional and
        combines with the others (AND). Never touches unpublished/draft profiles.

        - `q`: free text ("React developer remote"). Whitespace-separated terms are ANDed; each
          must match the slug, name, title, bio, location, an experience role or a skill name
          (case-insensitive). The words remote/hybrid/onsite match the work mode instead.
        - `username`/`location`/`field`: legacy partial-match filters.
        - `job_type`: Work filter. `start`: "immediately" (notice 0) or "30" (notice <= 30 days).
        - `exp`: "0-2" / "3-5" / "6+" years, measured from the earliest experience start date.
        - `overlap_hours`: viewer's {start, end, timezone}; keeps candidates whose working hours
          overlap it.
        """
        stmt = (
            select(PublicChatfolio, CandidateProfile)
            .join(CandidateProfile, CandidateProfile.id == PublicChatfolio.profile_id)
            .where(PublicChatfolio.is_published.is_(True))
        )
        if username:
            stmt = stmt.where(PublicChatfolio.slug.ilike(f"%{username}%"))
        if location:
            stmt = stmt.where(CandidateProfile.location.ilike(f"%{location}%"))
        if job_type:
            stmt = stmt.where(CandidateProfile.job_type == job_type)
        if field:
            stmt = stmt.where(self._text_match(f"%{field.strip()}%", fields="role"))
        for term in (q or "").split():
            mode = {jt.value: jt for jt in JobType}.get(term.lower())
            if mode is not None:
                stmt = stmt.where(CandidateProfile.job_type == mode)
            else:
                stmt = stmt.where(self._text_match(f"%{term}%", fields="all"))

        if start == "immediately":
            stmt = stmt.where(CandidateProfile.notice_period_days == 0)
        elif start == "30":
            stmt = stmt.where(CandidateProfile.notice_period_days <= 30)

        if exp:
            earliest = (
                select(func.min(Experience.start_date))
                .where(Experience.profile_id == CandidateProfile.id)
                .scalar_subquery()
            )
            today = datetime.now(UTC).date()

            def ago(years: int) -> date:
                return today - timedelta(days=round(365.25 * years))

            if exp == "0-2":
                stmt = stmt.where(earliest > ago(2))
            elif exp == "3-5":
                stmt = stmt.where(earliest <= ago(3), earliest > ago(6))
            else:
                stmt = stmt.where(earliest <= ago(6))

        stmt = stmt.order_by(PublicChatfolio.published_at.desc())
        if not overlap_hours:
            stmt = stmt.limit(limit)
        rows = [(row[0], row[1]) for row in (await self._session.execute(stmt)).all()]
        if overlap_hours:
            rows = [r for r in rows if hours_overlap(r[1].work_hours, overlap_hours)][:limit]
        return await self._build_hits(rows)

    @staticmethod
    def _text_match(pattern: str, *, fields: Literal["all", "role"]):
        role_match = (
            select(Experience.id)
            .where(Experience.profile_id == CandidateProfile.id, Experience.role.ilike(pattern))
            .exists()
        )
        # Title is often empty (e.g. profile built from a CV), so also match any experience
        # role and the bio.
        clauses = [CandidateProfile.title.ilike(pattern), CandidateProfile.bio.ilike(pattern)]
        clauses.append(role_match)
        if fields == "all":
            skill_match = (
                select(Skill.id)
                .where(Skill.profile_id == CandidateProfile.id, Skill.name.ilike(pattern))
                .exists()
            )
            clauses += [
                skill_match,
                PublicChatfolio.slug.ilike(pattern),
                CandidateProfile.full_name.ilike(pattern),
                CandidateProfile.location.ilike(pattern),
            ]
        return or_(*clauses)

    async def _build_hits(
        self, rows: list[tuple[PublicChatfolio, CandidateProfile]]
    ) -> list[SearchHit]:
        if not rows:
            return []
        profile_ids = [p.id for _, p in rows]
        hits = {p.id: SearchHit(chatfolio=c, profile=p) for c, p in rows}

        sections: dict[uuid.UUID, dict[SectionType, str]] = {}
        result = await self._session.execute(
            select(PortfolioSection).where(
                PortfolioSection.profile_id.in_(profile_ids),
                PortfolioSection.status == SectionStatus.APPROVED,
                PortfolioSection.section_type.in_([SectionType.INTRO, SectionType.SUMMARY]),
            )
        )
        for sec in result.scalars():
            sections.setdefault(sec.profile_id, {})[sec.section_type] = sec.content
        for pid, hit in hits.items():
            by_type = sections.get(pid, {})
            hit.headline = _make_headline(
                by_type.get(SectionType.INTRO) or by_type.get(SectionType.SUMMARY)
            )

        today = datetime.now(UTC).date()
        result = await self._session.execute(
            select(Experience)
            .where(Experience.profile_id.in_(profile_ids))
            .order_by(Experience.start_date.desc().nulls_last())
        )
        earliest: dict[uuid.UUID, date] = {}
        for exp in result.scalars():
            hit = hits[exp.profile_id]
            if hit.current_company is None and exp.is_current:
                hit.current_company = exp.company
            if exp.start_date and (
                exp.profile_id not in earliest or exp.start_date < earliest[exp.profile_id]
            ):
                earliest[exp.profile_id] = exp.start_date
        for pid, first in earliest.items():
            hits[pid].years_experience = round(max((today - first).days, 0) / 365.25, 1)

        result = await self._session.execute(
            select(Skill).where(Skill.profile_id.in_(profile_ids))
        )
        for skill in result.scalars():
            names = hits[skill.profile_id].top_skills
            if len(names) < TOP_SKILLS_MAX:
                names.append(skill.name)

        return [hits[p.id] for _, p in rows]

    async def list_approved_sections(self, profile_id: uuid.UUID) -> dict[SectionType, str]:
        result = await self._session.execute(
            select(PortfolioSection).where(
                PortfolioSection.profile_id == profile_id,
                PortfolioSection.status == SectionStatus.APPROVED,
            )
        )
        return {section.section_type: section.content for section in result.scalars().all()}

    async def list_children(self, model: type[ChildT], profile_id: uuid.UUID) -> list[ChildT]:
        return list(await self._profile_repository.list_children(model, profile_id))

    async def get_latest_parsed_cv(self, profile_id: uuid.UUID) -> UploadedCV | None:
        result = await self._session.execute(
            select(UploadedCV)
            .where(UploadedCV.profile_id == profile_id, UploadedCV.status == CVStatus.PARSED)
            .order_by(UploadedCV.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def count_identified_recruiters(self, chatfolio_id: uuid.UUID) -> int:
        """Recruiters who volunteered at least their name or company during chat — not every
        session, most of which never mention either (RecruiterMetadata is best-effort and
        optional per §6.8, so most rows are all-null)."""
        result = await self._session.execute(
            select(func.count(RecruiterMetadata.id))
            .join(ChatSession, ChatSession.id == RecruiterMetadata.session_id)
            .where(
                ChatSession.chatfolio_id == chatfolio_id,
                or_(RecruiterMetadata.name.is_not(None), RecruiterMetadata.company.is_not(None)),
            )
        )
        return result.scalar_one()

    async def record_visit(self, chatfolio_id: uuid.UUID) -> None:
        self._session.add(PortfolioVisit(chatfolio_id=chatfolio_id))
        await self._session.flush()
