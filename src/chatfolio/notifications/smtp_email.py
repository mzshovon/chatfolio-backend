from email.message import EmailMessage

import aiosmtplib
import structlog

from chatfolio.config.settings import EmailSettings

logger = structlog.get_logger()


class SmtpEmailSender:
    def __init__(self, settings: EmailSettings) -> None:
        self._settings = settings

    async def send(self, *, to: str, subject: str, body: str) -> None:
        if not self._settings.smtp_host:
            logger.warning("email.smtp_not_configured", to=to, subject=subject)
            return

        message = EmailMessage()
        message["From"] = self._settings.from_address
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)

        await aiosmtplib.send(
            message,
            hostname=self._settings.smtp_host,
            port=self._settings.smtp_port,
            username=self._settings.smtp_username,
            password=(
                self._settings.smtp_password.get_secret_value()
                if self._settings.smtp_password
                else None
            ),
            start_tls=self._settings.smtp_use_tls,
        )
