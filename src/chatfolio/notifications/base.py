from typing import Protocol


class EmailSender(Protocol):
    async def send(self, *, to: str, subject: str, body: str) -> None: ...


class SmsSender(Protocol):
    async def send(self, *, to: str, message: str) -> None: ...
