from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class ChatfolioError(Exception):
    """Base class for all domain-level errors. Never raise HTTPException from services."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    message: str = "Something went wrong."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.message
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


class ValidationFailedError(ChatfolioError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    message = "Validation failed."


class ServiceUnavailableError(ChatfolioError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    message = "This feature is temporarily unavailable. Please try again later."


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ChatfolioError)
    async def handle_chatfolio_error(_: Request, exc: ChatfolioError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})
