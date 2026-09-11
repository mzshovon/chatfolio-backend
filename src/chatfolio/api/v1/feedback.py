from fastapi import APIRouter, Request, status

from chatfolio.api.deps import DbSessionDep
from chatfolio.core.rate_limit import limiter
from chatfolio.schemas.feedback import FeedbackResponse, FeedbackSubmitRequest
from chatfolio.services.feedback_service import FeedbackService

router = APIRouter(prefix="/public/feedback", tags=["public-feedback"])


@router.post("", response_model=FeedbackResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def submit_feedback(
    request: Request, payload: FeedbackSubmitRequest, session: DbSessionDep
) -> FeedbackResponse:
    feedback = await FeedbackService(session).submit(payload.nps_score, payload.message)
    return FeedbackResponse.model_validate(feedback)
