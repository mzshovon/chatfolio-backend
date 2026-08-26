import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from chatfolio.schemas.chat import ChatMessageResponse


class RecruiterMetadataResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str | None
    company: str | None
    role: str | None
    required_skills: str | None
    experience_expectation: str | None
    location_pref: str | None
    timeline: str | None


class ConversationSummaryResponse(BaseModel):
    id: uuid.UUID
    started_at: datetime
    last_active_at: datetime | None
    is_flagged: bool
    reviewed_by_candidate: bool
    message_count: int
    recruiter_metadata: RecruiterMetadataResponse | None


class ConversationDetailResponse(ConversationSummaryResponse):
    messages: list[ChatMessageResponse]


class DashboardAnalyticsResponse(BaseModel):
    portfolio_visitors_total: int
    # null when there's no prior 30-day period to compare against yet (e.g. a newly published
    # page) — don't render this as "0%" or "-100%", show "not enough data yet" instead.
    portfolio_visitors_delta_pct: int | None
    ai_tokens_used: int
    ai_tokens_monthly_quota: int
