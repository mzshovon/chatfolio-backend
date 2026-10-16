import hashlib

from fastapi import APIRouter, Request, Response

from chatfolio.api.deps import PricingServiceDep
from chatfolio.schemas.billing import PricingResponse

router = APIRouter(tags=["pricing"])


@router.get("/pricing", response_model=PricingResponse)
async def get_pricing(request: Request, service: PricingServiceDep) -> Response:
    """Public (no auth): plans exactly as the pricing cards render them. ETag + short
    Cache-Control let browsers/CDNs revalidate cheaply."""
    body = (await service.get_pricing()).model_dump_json()
    etag = f'"{hashlib.sha256(body.encode()).hexdigest()[:32]}"'
    headers = {"ETag": etag, "Cache-Control": "public, max-age=60"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)
