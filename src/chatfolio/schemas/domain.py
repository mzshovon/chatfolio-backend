import uuid

from pydantic import BaseModel, ConfigDict, Field


class AddDomainRequest(BaseModel):
    domain: str = Field(min_length=3, max_length=255)


class DomainResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    domain: str
    is_verified: bool
    verification_token: str
