from enum import StrEnum

from sqlalchemy import JSON, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from chatfolio.db.base import Base
from chatfolio.models.mixins import ProfileChildMixin, TimestampMixin


class CVStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PARSED = "parsed"
    FAILED = "failed"


class UploadedCV(ProfileChildMixin, TimestampMixin, Base):
    __tablename__ = "uploaded_cvs"

    file_key: Mapped[str] = mapped_column(String(512))
    file_type: Mapped[str] = mapped_column(String(10))
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[CVStatus] = mapped_column(
        Enum(CVStatus, name="cv_status"), default=CVStatus.PENDING
    )
    raw_text: Mapped[str | None] = mapped_column(Text, default=None)
    parsed_json: Mapped[dict[str, object] | None] = mapped_column(JSON, default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
