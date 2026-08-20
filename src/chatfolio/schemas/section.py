import uuid

from pydantic import BaseModel, ConfigDict, Field

from chatfolio.models.portfolio_section import GeneratedBy, SectionStatus, SectionType


class SectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    section_type: SectionType
    content: str
    status: SectionStatus
    generated_by: GeneratedBy
    version: int


class SectionUpdateRequest(BaseModel):
    content: str = Field(min_length=1)
