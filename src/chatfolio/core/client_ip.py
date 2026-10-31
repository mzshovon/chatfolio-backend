import ipaddress

from fastapi import Request


def get_client_ip(request: Request, *, trust_proxy_headers: bool) -> str | None:
    """The caller's IP. With `trust_proxy_headers`, the left-most X-Forwarded-For entry (the
    original client) wins; otherwise only the socket peer is believed, since the header is
    client-controlled unless a proxy we run overwrites it."""
    if trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return request.client.host if request.client else None


def is_public_ip(value: str | None) -> bool:
    """False for missing/garbage input and for loopback, private, link-local etc. addresses,
    which no geolocation provider can place."""
    if not value:
        return False
    try:
        return ipaddress.ip_address(value).is_global
    except ValueError:
        return False
