import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from chatfolio.models.cv import CVStatus
from chatfolio.models.user import UserRole

PERMISSION_KEY_PATTERN = r"^[a-z0-9]+\.[a-z0-9]+$"


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
    total_portfolio_visitors: int
    recruiters_engaged: int
    ai_tokens_used: int
    ai_tokens_monthly_quota: int


class AdminCreateUserRequest(BaseModel):
    email: EmailStr
    role: UserRole = UserRole.CANDIDATE
    is_active: bool = True


class AdminUpdateUserRequest(BaseModel):
    email: EmailStr | None = None
    role: UserRole | None = None
    is_active: bool | None = None


class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str
    permissions: list[str]


class RoleCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    permissions: list[str] = Field(default_factory=list)


class RoleUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    permissions: list[str] | None = None


class PermissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    description: str
    used_by_roles_count: int


class PermissionCreateRequest(BaseModel):
    key: str = Field(pattern=PERMISSION_KEY_PATTERN)
    description: str = ""


class PermissionUpdateRequest(BaseModel):
    # `key` deliberately excluded — see Permission's model docstring.
    description: str | None = None
