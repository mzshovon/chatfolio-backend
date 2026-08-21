import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from chatfolio.models.chat import MessageRole


class StartSessionResponse(BaseModel):
    session_id: uuid.UUID


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class ChatMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: MessageRole
    content: str
    intent: str | None
    created_at: datetime
