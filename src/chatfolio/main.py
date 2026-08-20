from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from chatfolio.api.v1.auth import router as auth_router
from chatfolio.api.v1.cv import router as cv_router
from chatfolio.api.v1.health import router as health_router
from chatfolio.api.v1.profiles import router as profiles_router
from chatfolio.api.v1.sections import router as sections_router
from chatfolio.config.logging import configure_logging
from chatfolio.config.settings import get_settings
from chatfolio.core.exceptions import register_exception_handlers


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.env)

    app = FastAPI(title="Chatfolio API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.security.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    app.include_router(health_router, prefix="/v1")
    app.include_router(auth_router, prefix="/v1")
    app.include_router(profiles_router, prefix="/v1")
    app.include_router(cv_router, prefix="/v1")
    app.include_router(sections_router, prefix="/v1")

    return app


app = create_app()
