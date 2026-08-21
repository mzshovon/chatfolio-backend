import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from chatfolio.models.cv import CVStatus


class AdminChatfolioResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    is_published: bool
    published_at: datetime | None
    owner_email: str


class AdminCVJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: CVStatus
    error_message: str | None
    owner_email: str
    created_at: datetime


class AdminMetricsResponse(BaseModel):
    total_users: int
    total_candidates: int
    published_chatfolios: int
    total_chat_sessions: int
    total_chat_messages: int
    flagged_chat_sessions: int
    cv_parse_success_count: int
    cv_parse_failed_count: int
