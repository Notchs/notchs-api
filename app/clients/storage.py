"""Private file storage on S3 (customer profile pictures).

The bucket stays private ("Block all public access" on). Files are read through signed links that
work for a few minutes, so a link copied from the app stops working soon after. boto3 is
synchronous, so every call runs in a worker thread. Keys are logged, never file contents.

Without AWS_S3_BUCKET and both keys the storage is "not configured": uploads answer
`storage.not_configured` and profiles simply show no photo.
"""

import asyncio
from functools import cached_property
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

SIGNED_URL_SECONDS = 15 * 60


class StorageError(Exception):
    """S3 refused or could not be reached."""


class PhotoStorage:
    @property
    def configured(self) -> bool:
        return bool(
            settings.AWS_S3_BUCKET
            and settings.AWS_ACCESS_KEY_ID
            and settings.AWS_SECRET_ACCESS_KEY
            and settings.AWS_SECRET_ACCESS_KEY.get_secret_value()
        )

    @cached_property
    def _client(self) -> Any:
        import boto3  # imported here: only needed once storage is set up

        assert settings.AWS_SECRET_ACCESS_KEY is not None
        return boto3.client(
            "s3",
            region_name=settings.AWS_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY.get_secret_value(),
        )

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await self._call(
            "put_object",
            Bucket=settings.AWS_S3_BUCKET,
            Key=key,
            Body=data,
            ContentType=content_type,
            CacheControl="private, max-age=900",
        )

    async def delete(self, key: str) -> None:
        await self._call("delete_object", Bucket=settings.AWS_S3_BUCKET, Key=key)

    async def signed_url(self, key: str) -> str:
        return await asyncio.to_thread(
            self._client.generate_presigned_url,
            "get_object",
            Params={"Bucket": settings.AWS_S3_BUCKET, "Key": key},
            ExpiresIn=SIGNED_URL_SECONDS,
        )

    async def _call(self, method: str, **kwargs: Any) -> None:
        try:
            await asyncio.to_thread(getattr(self._client, method), **kwargs)
        except Exception as exc:  # botocore errors, network
            logger.exception("S3 %s failed for %s", method, kwargs.get("Key"))
            raise StorageError(method) from exc


_storage = PhotoStorage()


def get_photo_storage() -> PhotoStorage:
    """FastAPI dependency (overridden in tests)."""
    return _storage
