from datetime import datetime

from pydantic import BaseModel, Field

SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$"


class PortfolioSettingsUpdateRequest(BaseModel):
    slug: str | None = Field(default=None, pattern=SLUG_PATTERN, min_length=3, max_length=63)
    contact_cta_config: dict[str, str] | None = None
    cv_downloadable: bool | None = None


class PortfolioSettingsResponse(BaseModel):
    slug: str
    subdomain: str
    previous_slug: str | None
    is_published: bool
    published_at: datetime | None
    contact_cta_config: dict[str, str]
    cv_downloadable: bool
