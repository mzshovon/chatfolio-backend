from fastapi import APIRouter, Request, Response

from chatfolio.api.deps import GeoLocationServiceDep, SettingsDep
from chatfolio.core.client_ip import get_client_ip
from chatfolio.core.rate_limit import limiter
from chatfolio.schemas.viewer import ViewerLocationResponse

router = APIRouter(prefix="/public/viewer", tags=["public"])


@router.get("/location", response_model=ViewerLocationResponse)
@limiter.limit("60/minute")
async def get_viewer_location(
    request: Request,
    response: Response,
    service: GeoLocationServiceDep,
    settings: SettingsDep,
) -> ViewerLocationResponse:
    """Public (no auth): where the caller is, resolved from the request's IP — e.g. "Dhaka,
    Bangladesh (GMT+6)". Fields are null when the location can't be determined."""
    ip = get_client_ip(request, trust_proxy_headers=settings.geoip.trust_proxy_headers)
    # Per-visitor answer: keep shared caches/CDNs from serving one visitor's location to another.
    response.headers["Cache-Control"] = "private, max-age=300"
    return await service.locate(ip)
