from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from chatfolio.db.base import Base
from chatfolio.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Role(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Admin-manageable data only — not wired into real authorization. Access control is still
    entirely `User.role` (`candidate`/`admin`) and the `require_admin` dependency; this table
    exists so the admin Roles/Permissions pages have something real to read and write instead of
    local-only mock state, nothing more."""

    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(500), default="")
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)


class Permission(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Same non-enforcing status as Role — see its docstring. `key` is immutable after creation
    (enforced at the schema level, not here) to avoid needing to migrate every Role.permissions
    array that references it on a rename."""

    __tablename__ = "permissions"

    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(500), default="")
