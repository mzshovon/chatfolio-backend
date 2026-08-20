from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aioboto3
from botocore.exceptions import ClientError

from chatfolio.config.settings import StorageSettings


class S3StorageBackend:
    """Adapts S3-compatible object storage (AWS S3 in prod, MinIO locally) behind StorageBackend."""

    def __init__(self, settings: StorageSettings) -> None:
        self._settings = settings
        self._session = aioboto3.Session()

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[Any]:
        async with self._session.client(
            "s3",
            endpoint_url=self._settings.endpoint_url,
            region_name=self._settings.region,
            aws_access_key_id=self._settings.access_key.get_secret_value(),
            aws_secret_access_key=self._settings.secret_key.get_secret_value(),
        ) as client:
            yield client

    async def _ensure_bucket(self, client: Any) -> None:
        try:
            await client.head_bucket(Bucket=self._settings.bucket)
        except ClientError:
            await client.create_bucket(Bucket=self._settings.bucket)

    async def upload(self, *, key: str, content: bytes, content_type: str) -> None:
        async with self._client() as client:
            await self._ensure_bucket(client)
            await client.put_object(
                Bucket=self._settings.bucket, Key=key, Body=content, ContentType=content_type
            )

    async def download(self, key: str) -> bytes:
        async with self._client() as client:
            response = await client.get_object(Bucket=self._settings.bucket, Key=key)
            body: bytes = await response["Body"].read()
            return body

    async def generate_download_url(self, key: str, expires_in: int = 3600) -> str:
        async with self._client() as client:
            url: str = await client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._settings.bucket, "Key": key},
                ExpiresIn=expires_in,
            )
            return url

    async def delete(self, key: str) -> None:
        async with self._client() as client:
            await client.delete_object(Bucket=self._settings.bucket, Key=key)
