from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    # Harmless to send over plain HTTP (browsers only honor it on HTTPS responses) — this API
    # is expected to sit behind a TLS-terminating proxy in staging/production.
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """No CSP: this is a pure JSON API with no HTML responses of its own, so the usual
    script/style-source directives don't apply — the frontend serving actual pages owns its own.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for header, value in _HEADERS.items():
            response.headers[header] = value
        return response
