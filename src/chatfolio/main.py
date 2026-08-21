import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from chatfolio.api.v1.auth import router as auth_router
from chatfolio.api.v1.cv import router as cv_router
from chatfolio.api.v1.dashboard import router as dashboard_router
from chatfolio.api.v1.health import router as health_router
from chatfolio.api.v1.portfolio_settings import router as portfolio_settings_router
from chatfolio.api.v1.profiles import router as profiles_router
from chatfolio.api.v1.public_chat import router as public_chat_router
from chatfolio.api.v1.public_portfolio import router as public_portfolio_router
from chatfolio.api.v1.sections import router as sections_router
from chatfolio.config.logging import configure_logging
from chatfolio.config.settings import Environment, get_settings
from chatfolio.core.exceptions import register_exception_handlers
from chatfolio.core.rate_limit import limiter
from chatfolio.vectorstore.local_embedder import embed_texts

_DEFAULT_JWT_SECRET = "change-me-in-env-generate-a-real-random-secret-please"


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Pre-warm the local embedding model before accepting traffic — it downloads an ~80MB ONNX
    # model lazily on its *first* call otherwise, which would mean the first real recruiter
    # message hangs (possibly for minutes) waiting on that download instead of getting a reply.
    # workers/worker.py does the same thing for the same reason — this container has its own
    # separate filesystem/cache, so pre-warming one process never pre-warms the other. Never
    # runs under tests (httpx.ASGITransport doesn't trigger lifespan events), which is fine:
    # tests override get_embed_texts with a fake before any real embedder code would run.
    await asyncio.to_thread(embed_texts, ["warmup"])
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.env)

    if (
        settings.env != Environment.LOCAL
        and settings.security.jwt_secret.get_secret_value() == _DEFAULT_JWT_SECRET
    ):
        raise RuntimeError(
            "SECURITY_JWT_SECRET is still the default placeholder outside a local environment. "
            "Set a real, random secret before starting the app."
        )

    app = FastAPI(title="Chatfolio API", version="0.1.0", lifespan=_lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.security.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    async def handle_rate_limit_exceeded(request: Request, exc: Exception) -> Response:
        assert isinstance(exc, RateLimitExceeded)  # exception_handler's contract, not ours
        return _rate_limit_exceeded_handler(request, exc)

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, handle_rate_limit_exceeded)
    app.add_middleware(SlowAPIMiddleware)

    app.include_router(health_router, prefix="/v1")
    app.include_router(auth_router, prefix="/v1")
    app.include_router(profiles_router, prefix="/v1")
    app.include_router(cv_router, prefix="/v1")
    app.include_router(sections_router, prefix="/v1")
    app.include_router(portfolio_settings_router, prefix="/v1")
    app.include_router(public_portfolio_router, prefix="/v1")
    app.include_router(public_chat_router, prefix="/v1")
    app.include_router(dashboard_router, prefix="/v1")

    return app


app = create_app()
