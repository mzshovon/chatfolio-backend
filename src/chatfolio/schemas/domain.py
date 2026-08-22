import re
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Hostname per RFC 1123: dot-separated labels, each 1-63 chars, alphanumeric + hyphen (not
# leading/trailing), at least one dot (a bare label like "localhost" isn't a domain a candidate
# would point DNS at). Deliberately not stricter (no public-suffix-list check, no IDN handling)
# — this is a Phase-2 stub with no verification logic yet; the real validation gate is DNS
# ownership proof, whenever that gets built, not this regex.
_DOMAIN_PATTERN = re.compile(
    r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$"
)


class AddDomainRequest(BaseModel):
    domain: str = Field(min_length=3, max_length=255)

    @field_validator("domain")
    @classmethod
    def _validate_domain_format(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _DOMAIN_PATTERN.match(normalized):
            raise ValueError("Must be a valid domain name, e.g. www.example.com.")
        return normalized


class DomainResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    domain: str
    is_verified: bool
    verification_token: str
