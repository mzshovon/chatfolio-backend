import uuid

from pydantic import BaseModel, ConfigDict

from chatfolio.models.cv import CVStatus


class CVResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: CVStatus
    file_type: str
    size_bytes: int
    error_message: str | None
    parsed_json: dict[str, object] | None
