from __future__ import annotations

import json
from typing import Any

import aioboto3
import structlog
from botocore.config import Config as BotoConfig

log = structlog.get_logger()


class S3Storage:
    """Thin async wrapper around aioboto3 for S3 operations."""

    def __init__(
        self,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "us-east-1",
    ) -> None:
        self._endpoint_url = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self._bucket = bucket
        self._region = region
        self._session = aioboto3.Session()
        self._config = BotoConfig(signature_version="s3v4")

    async def put_json(self, key: str, data: dict[str, Any]) -> None:
        body = json.dumps(data, default=str).encode()
        async with self._client() as s3:
            await s3.put_object(Bucket=self._bucket, Key=key, Body=body)
        log.debug("s3.put", key=key, size=len(body))

    async def get_json(self, key: str) -> dict[str, Any] | None:
        async with self._client() as s3:
            try:
                resp = await s3.get_object(Bucket=self._bucket, Key=key)
                body = await resp["Body"].read()
                return json.loads(body)
            except s3.exceptions.NoSuchKey:
                return None
            except Exception:
                # ClientError from a 404 also lands here
                log.warning("s3.get.miss", key=key)
                return None

    async def find_key_by_suffix(self, prefix: str, suffix: str) -> str | None:
        """List objects under *prefix* and return the first key ending with *suffix*."""
        async with self._client() as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(
                Bucket=self._bucket, Prefix=prefix
            ):
                for obj in page.get("Contents", []):
                    if obj["Key"].endswith(suffix):
                        return obj["Key"]
        return None

    def _client(self):
        return self._session.client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name=self._region,
            config=self._config,
        )

    async def list_keys(self, prefix: str) -> list[str]:
        """List all object keys under *prefix*.

        Returns a list of keys (str)."""
        keys: list[str] = []
        async with self._client() as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"])
        return keys
