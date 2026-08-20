"""Import every model module here so Base.metadata is fully populated wherever it's imported."""

from chatfolio.models import cv, embedding, portfolio_section, profile, user  # noqa: F401
