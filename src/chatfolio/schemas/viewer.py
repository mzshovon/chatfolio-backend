from pydantic import BaseModel


class ViewerLocationResponse(BaseModel):
    """Every field is null when the visitor's location can't be determined (private/local IP,
    lookup disabled or provider down) so the UI can simply hide the label."""

    city: str | None = None
    country: str | None = None
    country_code: str | None = None
    timezone: str | None = None  # IANA id, e.g. "Asia/Dhaka"
    utc_offset: str | None = None  # e.g. "GMT+6", "GMT+5:30"
    display: str | None = None  # e.g. "Dhaka, Bangladesh (GMT+6)"
