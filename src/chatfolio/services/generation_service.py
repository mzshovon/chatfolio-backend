import uuid

import structlog

from chatfolio.config.settings import LLMTask
from chatfolio.core.exceptions import ServiceUnavailableError
from chatfolio.entitlements.catalog import FeatureKey
from chatfolio.llm.base import LLMCompletion, LLMFactory
from chatfolio.llm.prompts.section_generation import SECTION_GENERATION_SYSTEM_PROMPTS
from chatfolio.models.cv import CVStatus, UploadedCV
from chatfolio.models.portfolio_section import (
    GeneratedBy,
    PortfolioSection,
    SectionStatus,
    SectionType,
)
from chatfolio.models.profile import CandidateProfile, Education, Experience, Project, Skill
from chatfolio.models.user import User
from chatfolio.services.entitlement_guard import EntitlementGuard
from chatfolio.services.profile_service import ProfileService

RAW_CV_CONTEXT_CHAR_LIMIT = 2000
DESCRIPTION_CHAR_LIMIT = 150
BIO_CHAR_LIMIT = 300
MAX_EXPERIENCES = 4
MAX_PROJECTS = 3
MAX_SKILLS = 15

logger = structlog.get_logger(__name__)


class GenerationService:
    def __init__(
        self, profile_service: ProfileService, llm_factory: LLMFactory, guard: EntitlementGuard
    ) -> None:
        self._profile_service = profile_service
        self._llm_factory = llm_factory
        self._guard = guard

    async def list_sections(self, user: User) -> list[PortfolioSection]:
        existing = {
            section.section_type: section
            for section in await self._profile_service.list_children(user, PortfolioSection)
        }
        for section_type in SectionType:
            if section_type not in existing:
                existing[section_type] = await self._generate(user, section_type)
        return [existing[section_type] for section_type in SectionType]

    async def regenerate(self, user: User, section_id: uuid.UUID) -> PortfolioSection:
        section = await self._profile_service.get_owned_child(user, PortfolioSection, section_id)
        return await self._generate(user, section.section_type, existing=section)

    async def update_content(
        self, user: User, section_id: uuid.UUID, content: str
    ) -> PortfolioSection:
        return await self._profile_service.update_child(
            user,
            PortfolioSection,
            section_id,
            {"content": content, "generated_by": GeneratedBy.MANUAL, "status": SectionStatus.DRAFT},
        )

    async def approve(self, user: User, section_id: uuid.UUID) -> PortfolioSection:
        return await self._profile_service.update_child(
            user, PortfolioSection, section_id, {"status": SectionStatus.APPROVED}
        )

    async def _generate(
        self, user: User, section_type: SectionType, existing: PortfolioSection | None = None
    ) -> PortfolioSection:
        profile = await self._profile_service.get_or_create_for_user(user)
        context = await self._build_context(user)
        # Owner-facing, so a spent token budget is reported plainly (403 LIMIT_REACHED).
        await self._guard.ensure_open(user.id, FeatureKey.AI_TOKENS_LIMIT)
        completion = await self._complete(section_type, context, user.id)
        content = completion.content.strip()
        profile.ai_tokens_used += completion.tokens_used
        await self._guard.add_usage(user.id, FeatureKey.AI_TOKENS_LIMIT, completion.tokens_used)

        if existing is not None:
            return await self._profile_service.update_child(
                user,
                PortfolioSection,
                existing.id,
                {
                    "content": content,
                    "generated_by": GeneratedBy.AI,
                    "status": SectionStatus.DRAFT,
                    "version": existing.version + 1,
                },
            )

        section = PortfolioSection(
            section_type=section_type,
            content=content,
            generated_by=GeneratedBy.AI,
            status=SectionStatus.DRAFT,
            version=1,
        )
        return await self._profile_service.add_child(user, section)

    async def _complete(
        self, section_type: SectionType, context: str, owner_id: uuid.UUID
    ) -> LLMCompletion:
        try:
            provider = self._llm_factory.for_task(LLMTask.GENERATION, owner_id=owner_id)
            return await provider.complete(
                system=SECTION_GENERATION_SYSTEM_PROMPTS[section_type],
                messages=[{"role": "user", "content": context}],
            )
        except Exception as exc:
            logger.exception("section_generation.failed", section_type=section_type.value)
            raise ServiceUnavailableError(
                "AI section generation is not available right now. Please try again later."
            ) from exc

    async def _build_context(self, user: User) -> str:
        profile = await self._profile_service.get_or_create_for_user(user)
        experiences = await self._profile_service.list_children(user, Experience)
        projects = await self._profile_service.list_children(user, Project)
        skills = await self._profile_service.list_children(user, Skill)
        education = await self._profile_service.list_children(user, Education)

        lines = self._profile_summary_lines(profile)
        lines += self._experience_lines(experiences)
        lines += self._project_lines(projects)
        lines += self._skill_lines(skills)
        lines += self._education_lines(education)

        if not experiences and not projects and not skills:
            raw_cv_text = await self._latest_parsed_cv_text(user)
            if raw_cv_text:
                lines.append("CV text:")
                lines.append(raw_cv_text[:RAW_CV_CONTEXT_CHAR_LIMIT])

        return "\n".join(lines) or "No profile data."

    @staticmethod
    def _clip(text: str | None, limit: int) -> str:
        text = " ".join((text or "").split())
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

    @classmethod
    def _profile_summary_lines(cls, profile: CandidateProfile) -> list[str]:
        fields = {
            "Name": profile.full_name,
            "Title": profile.title,
            "Bio": cls._clip(profile.bio, BIO_CHAR_LIMIT),
        }
        return [f"{label}: {value}" for label, value in fields.items() if value]

    @classmethod
    def _experience_lines(cls, experiences: list[Experience]) -> list[str]:
        if not experiences:
            return []
        lines = ["Experience:"]
        for exp in experiences[:MAX_EXPERIENCES]:
            end = "present" if exp.is_current else exp.end_date or "?"
            period = f"{exp.start_date or '?'}-{end}"
            desc = cls._clip(exp.description, DESCRIPTION_CHAR_LIMIT)
            lines.append(f"- {exp.role} @ {exp.company} ({period})" + (f": {desc}" if desc else ""))
        return lines

    @classmethod
    def _project_lines(cls, projects: list[Project]) -> list[str]:
        if not projects:
            return []
        lines = ["Projects:"]
        for project in projects[:MAX_PROJECTS]:
            desc = cls._clip(project.description, DESCRIPTION_CHAR_LIMIT)
            tech = ", ".join(project.tech_stack)
            line = f"- {project.title}" + (f": {desc}" if desc else "")
            lines.append(line + (f" [{tech}]" if tech else ""))
        return lines

    @staticmethod
    def _skill_lines(skills: list[Skill]) -> list[str]:
        if not skills:
            return []
        return ["Skills: " + ", ".join(skill.name for skill in skills[:MAX_SKILLS])]

    @staticmethod
    def _education_lines(education: list[Education]) -> list[str]:
        if not education:
            return []
        entries = []
        for entry in education:
            subject = " ".join(part for part in (entry.degree, entry.field) if part)
            entries.append(f"{subject} @ {entry.institution}" if subject else entry.institution)
        return ["Education: " + "; ".join(entries)]

    async def _latest_parsed_cv_text(self, user: User) -> str | None:
        cvs = await self._profile_service.list_children(user, UploadedCV)
        parsed = [cv for cv in cvs if cv.status == CVStatus.PARSED and cv.raw_text]
        if not parsed:
            return None
        latest = max(parsed, key=lambda cv: cv.created_at)
        return latest.raw_text
