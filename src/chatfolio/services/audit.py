from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from chatfolio.models.audit_log import AdminAuditLog
from chatfolio.models.user import User


async def record_admin_action(
    session: AsyncSession,
    admin: User,
    action: str,
    target_type: str,
    target_id: object,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AdminAuditLog(
            admin_user_id=admin.id,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            details=details,
        )
    )
    await session.flush()
