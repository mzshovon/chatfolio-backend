import sentry_sdk
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from chatfolio.config.settings import Environment, ObservabilitySettings


def configure_sentry(settings: ObservabilitySettings, env: Environment) -> None:
    if not settings.sentry_dsn:
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=env.value,
        traces_sample_rate=settings.sentry_traces_sample_rate,
    )


def configure_metrics(app: FastAPI, settings: ObservabilitySettings) -> None:
    if not settings.metrics_enabled:
        return
    # .instrument() must run before the app starts serving (it wraps every route); .expose()
    # adds the /metrics endpoint itself. Excluded from the OpenAPI schema — it's a scrape
    # target for Prometheus, not part of the public API surface.
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
