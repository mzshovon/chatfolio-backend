import uuid
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.core.exceptions import ConflictError, NotFoundError, ValidationFailedError
from chatfolio.core.security import generate_opaque_token, hash_password
from chatfolio.models.audit_log import AdminAuditLog
from chatfolio.models.chat import ChatMessage, ChatSession, RecruiterMetadata
from chatfolio.models.chatfolio import PortfolioVisit, PublicChatfolio
from chatfolio.models.cv import CVStatus, UploadedCV
from chatfolio.models.profile import DEFAULT_AI_TOKENS_MONTHLY_QUOTA, CandidateProfile
from chatfolio.models.user import User, UserRole
from chatfolio.notifications.base import EmailSender
from chatfolio.workers.queue import JobQueue

DEFAULT_PAGE_SIZE = 20


class AdminService:
    """Unrestricted, cross-candidate reads/mutations for the admin surface — deliberately does
    not go through the owner-scoped ProfileRepository/ProfileService helpers the candidate-facing
    services use, since an admin is by definition not scoped to one profile. Every mutation here
    writes an AdminAuditLog row (§6.11's moderation-readiness requirement).
    """

    def __init__(self, session: AsyncSession, job_queue: JobQueue) -> None:
        self._session = session
        self._job_queue = job_queue

    async def list_users(
        self, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> list[User]:
        result = await self._session.execute(
            select(User).order_by(User.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def list_chatfolios(
        self, *, is_published: bool | None = None, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> list[tuple[PublicChatfolio, str]]:
        stmt = (
            select(PublicChatfolio, User.email)
            .join(CandidateProfile, CandidateProfile.id == PublicChatfolio.profile_id)
            .join(User, User.id == CandidateProfile.user_id)
        )
        if is_published is not None:
            stmt = stmt.where(PublicChatfolio.is_published.is_(is_published))
        stmt = stmt.order_by(PublicChatfolio.created_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return [(chatfolio, email) for chatfolio, email in result.all()]

    async def list_failed_cv_jobs(
        self, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> list[tuple[UploadedCV, str]]:
        stmt = (
            select(UploadedCV, User.email)
            .join(CandidateProfile, CandidateProfile.id == UploadedCV.profile_id)
            .join(User, User.id == CandidateProfile.user_id)
            .where(UploadedCV.status == CVStatus.FAILED)
            .order_by(UploadedCV.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [(cv, email) for cv, email in result.all()]

    async def retry_cv_job(self, admin: User, cv_id: uuid.UUID) -> tuple[UploadedCV, str]:
        cv = await self._session.get(UploadedCV, cv_id)
        if cv is None:
            raise NotFoundError("CV job not found.")
        if cv.status != CVStatus.FAILED:
            raise ValidationFailedError("Only a failed upload can be retried.")

        cv.status = CVStatus.PENDING
        cv.error_message = None
        await self._session.flush()
        await self._job_queue.enqueue_job("parse_cv_job", str(cv.id))
        await self._log(admin, "cv_job.retry", "uploaded_cv", str(cv.id))
        return cv, await self._owner_email_for_profile(cv.profile_id)

    async def unpublish_chatfolio(
        self, admin: User, chatfolio_id: uuid.UUID
    ) -> tuple[PublicChatfolio, str]:
        chatfolio = await self._session.get(PublicChatfolio, chatfolio_id)
        if chatfolio is None:
            raise NotFoundError("Chatfolio not found.")

        chatfolio.is_published = False
        await self._session.flush()
        await self._log(admin, "chatfolio.unpublish", "public_chatfolio", str(chatfolio.id))
        return chatfolio, await self._owner_email_for_profile(chatfolio.profile_id)

    async def get_user(self, user_id: uuid.UUID) -> User:
        user = await self._session.get(User, user_id)
        if user is None:
            raise NotFoundError("User not found.")
        return user

    async def create_user(
        self,
        admin: User,
        email: str,
        role: UserRole,
        is_active: bool,
        *,
        email_sender: EmailSender,
    ) -> User:
        existing = await self._session.execute(select(User).where(User.email == email))
        if existing.scalar_one_or_none() is not None:
            raise ConflictError("An account with this email already exists.")

        # Long and random, not a frontend-supplied "temporary password" field — the account owner
        # never needs to type this themselves, only follow the emailed instructions to sign in
        # and set their own password (§2.4's forgot/reset-password flow already exists for that).
        temporary_password = generate_opaque_token()
        user = User(
            email=email,
            hashed_password=hash_password(temporary_password),
            role=role,
            is_active=is_active,
        )
        self._session.add(user)
        await self._session.flush()
        await self._log(admin, "user.create", "user", str(user.id))

        await email_sender.send(
            to=email,
            subject="Your Chatfolio account has been created",
            body=(
                "An administrator created a Chatfolio account for you.\n\n"
                f"Email: {email}\n"
                f"Temporary password: {temporary_password}\n\n"
                "Please sign in and change your password as soon as possible."
            ),
        )
        return user

    async def update_user(
        self, admin: User, user_id: uuid.UUID, updates: dict[str, object]
    ) -> User:
        user = await self.get_user(user_id)

        new_email = updates.get("email")
        if isinstance(new_email, str) and new_email != user.email:
            existing = await self._session.execute(select(User).where(User.email == new_email))
            if existing.scalar_one_or_none() is not None:
                raise ConflictError("An account with this email already exists.")

        for field, value in updates.items():
            setattr(user, field, value)
        await self._session.flush()
        await self._log(admin, "user.update", "user", str(user.id))
        return user

    async def delete_user(self, admin: User, user_id: uuid.UUID) -> None:
        if user_id == admin.id:
            raise ValidationFailedError("You cannot delete your own account.")

        user = await self.get_user(user_id)
        await self._log(admin, "user.delete", "user", str(user.id))
        await self._session.delete(user)
        await self._session.flush()

    async def _owner_email_for_profile(self, profile_id: uuid.UUID) -> str:
        result = await self._session.execute(
            select(User.email)
            .join(CandidateProfile, CandidateProfile.user_id == User.id)
            .where(CandidateProfile.id == profile_id)
        )
        return result.scalar_one()

    async def get_metrics(self) -> dict[str, int]:
        return {
            "total_users": await self._count(select(func.count()).select_from(User)),
            "total_candidates": await self._count(
                select(func.count()).select_from(User).where(User.role == UserRole.CANDIDATE)
            ),
            "published_chatfolios": await self._count(
                select(func.count())
                .select_from(PublicChatfolio)
                .where(PublicChatfolio.is_published.is_(True))
            ),
            "total_chat_sessions": await self._count(select(func.count()).select_from(ChatSession)),
            "total_chat_messages": await self._count(select(func.count()).select_from(ChatMessage)),
            "flagged_chat_sessions": await self._count(
                select(func.count()).select_from(ChatSession).where(ChatSession.is_flagged.is_(True))
            ),
            "cv_parse_success_count": await self._count(
                select(func.count())
                .select_from(UploadedCV)
                .where(UploadedCV.status == CVStatus.PARSED)
            ),
            "cv_parse_failed_count": await self._count(
                select(func.count())
                .select_from(UploadedCV)
                .where(UploadedCV.status == CVStatus.FAILED)
            ),
            "total_portfolio_visitors": await self._count(
                select(func.count()).select_from(PortfolioVisit)
            ),
            "recruiters_engaged": await self._count(
                select(func.count())
                .select_from(RecruiterMetadata)
                .where(
                    or_(
                        RecruiterMetadata.name.is_not(None),
                        RecruiterMetadata.company.is_not(None),
                    )
                )
            ),
            "ai_tokens_used": await self._sum_ai_tokens_used(),
            "ai_tokens_monthly_quota": await self._sum_ai_tokens_monthly_quota(),
        }

    async def _count(self, stmt: Select[Any]) -> int:
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def _sum_ai_tokens_used(self) -> int:
        result = await self._session.execute(select(func.sum(CandidateProfile.ai_tokens_used)))
        return int(result.scalar_one() or 0)

    async def _sum_ai_tokens_monthly_quota(self) -> int:
        # usage_limits is per-profile JSON, not a plain column — summing "quota, falling back to
        # the platform default when a profile hasn't set one" isn't expressible as a single SQL
        # aggregate, so it's a Python sum over admin-scale data (one profile per candidate).
        result = await self._session.execute(select(CandidateProfile.usage_limits))
        return sum(
            (usage_limits or {}).get("ai_tokens_monthly_quota", DEFAULT_AI_TOKENS_MONTHLY_QUOTA)
            for (usage_limits,) in result.all()
        )

    async def _log(self, admin: User, action: str, target_type: str, target_id: str) -> None:
        self._session.add(
            AdminAuditLog(
                admin_user_id=admin.id, action=action, target_type=target_type, target_id=target_id
            )
        )
        await self._session.flush()
