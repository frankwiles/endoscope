from __future__ import annotations

import json
from typing import Any

import aioboto3
import structlog
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

log = structlog.get_logger()


class StorageError(Exception):
    """Infrastructure error from S3 — not a missing-key condition."""


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
            except ClientError as exc:
                status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
                if status == 404:
                    return None
                log.warning("s3.get.error", key=key, error=str(exc))
                raise StorageError(f"S3 error fetching {key!r}: {exc}") from exc
            except Exception as exc:
                log.warning("s3.get.error", key=key, error=str(exc))
                raise StorageError(f"S3 error fetching {key!r}: {exc}") from exc

    async def find_key_by_suffix(self, prefix: str, suffix: str) -> str | None:
        """List objects under *prefix* and return the first key ending with *suffix*."""
        try:
            async with self._client() as s3:
                paginator = s3.get_paginator("list_objects_v2")
                async for page in paginator.paginate(
                    Bucket=self._bucket, Prefix=prefix
                ):
                    for obj in page.get("Contents", []):
                        if obj["Key"].endswith(suffix):
                            return obj["Key"]
        except Exception as exc:
            log.warning("s3.list.error", prefix=prefix, error=str(exc))
            raise StorageError(f"S3 error listing {prefix!r}: {exc}") from exc
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

        Returns a list of keys (str).
        """
        keys: list[str] = []
        try:
            async with self._client() as s3:
                paginator = s3.get_paginator("list_objects_v2")
                async for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                    for obj in page.get("Contents", []):
                        keys.append(obj["Key"])
        except Exception as exc:
            log.warning("s3.list.error", prefix=prefix, error=str(exc))
            raise StorageError(f"S3 error listing {prefix!r}: {exc}") from exc
        return keys



    async def delete_objects(self, keys: list[str]) -> None:
        """Batch delete S3 objects, chunking at the 1000-object S3 limit.

        Raises StorageError if any objects fail to delete.
        """
        if not keys:
            return
        async with self._client() as s3:
            for i in range(0, len(keys), 1000):
                chunk = keys[i : i + 1000]
                resp = await s3.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": [{"Key": k} for k in chunk]},
                )
                errors = resp.get("Errors", [])
                if errors:
                    failed_keys = [e["Key"] for e in errors]
                    log.warning("s3.delete.partial", failed=failed_keys)
                    raise StorageError(
                        f"S3 partial delete failure for keys: {failed_keys}"
                    )
                log.debug("s3.delete", count=len(chunk))

    async def get_object_bytes(self, key: str) -> bytes | None:
        """Download raw bytes for a key, or None if it doesn't exist."""
        async with self._client() as s3:
            try:
                resp = await s3.get_object(Bucket=self._bucket, Key=key)
                body = await resp["Body"].read()
                log.debug("s3.get_bytes", key=key, size=len(body))
                return body
            except ClientError as exc:
                status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
                if status == 404:
                    return None
                log.warning("s3.get_bytes.error", key=key, error=str(exc))
                raise StorageError(f"S3 error fetching {key!r}: {exc}") from exc
            except Exception as exc:
                log.warning("s3.get_bytes.error", key=key, error=str(exc))
                raise StorageError(f"S3 error fetching {key!r}: {exc}") from exc

    async def list_objects(self, prefix: str) -> list[dict]:
        """List all objects under *prefix* with key, size, and last_modified."""
        objects: list[dict] = []
        async with self._client() as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(
                Bucket=self._bucket, Prefix=prefix
            ):
                for obj in page.get("Contents", []):
                    objects.append({
                        "key": obj["Key"],
                        "size": obj["Size"],
                        "last_modified": obj["LastModified"],
                    })
        return objects

    async def generate_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        """Generate a presigned PUT URL for the given key."""
        async with self._client() as s3:
            url = await s3.generate_presigned_url(
                "put_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        return url
    async def check_ready(self) -> bool:
        """Lightweight S3 connectivity check."""
        try:
            async with self._client() as s3:
                await s3.list_objects_v2(
                    Bucket=self._bucket, MaxKeys=1
                )
            return True
        except Exception:
            log.warning("s3.health_check.failed")
            return False
