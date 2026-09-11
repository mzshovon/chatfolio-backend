"""Import every model module here so Base.metadata is fully populated wherever it's imported."""

from chatfolio.models import (  # noqa: F401
    audit_log,
    chat,
    chatfolio,
    cv,
    domain,
    embedding,
    feedback,
    portfolio_section,
    profile,
    rbac,
    user,
)
