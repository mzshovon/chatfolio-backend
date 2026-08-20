import uuid

import structlog

from chatfolio.config.settings import LLMTask
from chatfolio.core.exceptions import ServiceUnavailableError
from chatfolio.llm.base import LLMFactory
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
from chatfolio.services.profile_service import ProfileService

RAW_CV_CONTEXT_CHAR_LIMIT = 4000

logger = structlog.get_logger(__name__)


class GenerationService:
    def __init__(self, profile_service: ProfileService, llm_factory: LLMFactory) -> None:
        self._profile_service = profile_service
        self._llm_factory = llm_factory

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
        context = await self._build_context(user)
        content = await self._complete(section_type, context)

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

    async def _complete(self, section_type: SectionType, context: str) -> str:
        try:
            provider = self._llm_factory.for_task(LLMTask.GENERATION)
            response = await provider.complete(
                system=SECTION_GENERATION_SYSTEM_PROMPTS[section_type],
                messages=[{"role": "user", "content": context}],
            )
        except Exception as exc:
            logger.exception("section_generation.failed", section_type=section_type.value)
            raise ServiceUnavailableError(
                "AI section generation is not available right now. Please try again later."
            ) from exc
        return response.strip()

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
                lines.append("Additional context from the candidate's uploaded CV (raw text):")
                lines.append(raw_cv_text[:RAW_CV_CONTEXT_CHAR_LIMIT])

        return "\n".join(lines)

    @staticmethod
    def _profile_summary_lines(profile: CandidateProfile) -> list[str]:
        return [
            f"Full name: {profile.full_name or 'unknown'}",
            f"Title: {profile.title or 'unknown'}",
            f"Bio: {profile.bio or 'none provided'}",
            f"Location: {profile.location or 'unknown'}",
        ]

    @staticmethod
    def _experience_lines(experiences: list[Experience]) -> list[str]:
        if not experiences:
            return []
        lines = ["Experience:"]
        for exp in experiences:
            end = "present" if exp.is_current else (exp.end_date or "unknown end date")
            period = f"{exp.start_date or '?'} to {end}"
            lines.append(f"- {exp.role} at {exp.company} ({period}): {exp.description or ''}")
        return lines

    @staticmethod
    def _project_lines(projects: list[Project]) -> list[str]:
        if not projects:
            return []
        lines = ["Projects:"]
        for project in projects:
            tech = ", ".join(project.tech_stack)
            lines.append(f"- {project.title}: {project.description or ''} (tech: {tech})")
        return lines

    @staticmethod
    def _skill_lines(skills: list[Skill]) -> list[str]:
        if not skills:
            return []
        return ["Skills: " + ", ".join(skill.name for skill in skills)]

    @staticmethod
    def _education_lines(education: list[Education]) -> list[str]:
        if not education:
            return []
        lines = ["Education:"]
        for entry in education:
            lines.append(f"- {entry.degree or ''} in {entry.field or ''} at {entry.institution}")
        return lines

    async def _latest_parsed_cv_text(self, user: User) -> str | None:
        cvs = await self._profile_service.list_children(user, UploadedCV)
        parsed = [cv for cv in cvs if cv.status == CVStatus.PARSED and cv.raw_text]
        if not parsed:
            return None
        latest = max(parsed, key=lambda cv: cv.created_at)
        return latest.raw_text
