from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class ChatfolioError(Exception):
    """Base class for all domain-level errors. Never raise HTTPException from services."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    message: str = "Something went wrong."
    # Optional machine-readable code + extra fields, merged into the JSON body next to `detail`
    # so the frontends can branch on e.g. FEATURE_NOT_IN_PLAN without parsing prose.
    code: str | None = None

    def __init__(self, message: str | None = None, **extra: Any) -> None:
        self.message = message or self.message
        self.extra: dict[str, Any] = extra
        super().__init__(self.message)


class NotFoundError(ChatfolioError):
    status_code = status.HTTP_404_NOT_FOUND
    message = "Resource not found."


class ConflictError(ChatfolioError):
    status_code = status.HTTP_409_CONFLICT
    message = "Resource already exists."


class UnauthorizedError(ChatfolioError):
    status_code = status.HTTP_401_UNAUTHORIZED
    message = "Authentication required."


class ForbiddenError(ChatfolioError):
    status_code = status.HTTP_403_FORBIDDEN
    message = "You do not have access to this resource."


class FeatureNotInPlanError(ForbiddenError):
    code = "FEATURE_NOT_IN_PLAN"
    message = "Your current plan does not include this feature."

    def __init__(self, feature: str, message: str | None = None) -> None:
        super().__init__(message, feature=feature, upgrade_url="/pricing")


class ModelNotAllowedError(ForbiddenError):
    code = "MODEL_NOT_ALLOWED"
    message = "Your current plan cannot use this model."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message, upgrade_url="/pricing")


class ValidationFailedError(ChatfolioError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    message = "Validation failed."


class ServiceUnavailableError(ChatfolioError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    message = "This feature is temporarily unavailable. Please try again later."


class TooManyRequestsError(ChatfolioError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    message = "You're sending messages too quickly. Please slow down."


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ChatfolioError)
    async def handle_chatfolio_error(_: Request, exc: ChatfolioError) -> JSONResponse:
        content: dict[str, Any] = {"detail": exc.message}
        if exc.code is not None:
            content["code"] = exc.code
        content.update(exc.extra)
        return JSONResponse(status_code=exc.status_code, content=content)
