import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from chatfolio.models.feedback import FEEDBACK_MESSAGE_MAX_LENGTH, NPS_SCORE_MAX, NPS_SCORE_MIN


class FeedbackSubmitRequest(BaseModel):
    nps_score: int = Field(ge=NPS_SCORE_MIN, le=NPS_SCORE_MAX)
    message: str | None = Field(default=None, max_length=FEEDBACK_MESSAGE_MAX_LENGTH)


class FeedbackResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    nps_score: int
    message: str | None
    created_at: datetime
