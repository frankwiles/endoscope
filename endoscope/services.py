from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog
from endoscope.storage import S3Storage
from pydantic import BaseModel, Field

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


class SessionCreateRequest(BaseModel):
    """Payload sent by a client to create a new session."""

    project: str
    metadata: dict | None = None


class Session(BaseModel):
    """Core domain entity — a debug session."""

    session_id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    project: str
    metadata: dict | None = None

    @property
    def storage_prefix(self) -> str:
        """S3 key prefix:  <project>/yyyy/mm/dd/<iso-ts>--<session_id>/"""
        ts = self.timestamp
        date_part = ts.strftime("%Y/%m/%d")
        return f"{self.project}/{date_part}/{ts.isoformat()}--{self.session_id}"

    @property
    def metadata_key(self) -> str:
        return f"{self.storage_prefix}/metadata.json"


# ---------------------------------------------------------------------------
# Application service (DDD)
# ---------------------------------------------------------------------------


class SessionService:
    """Domain-driven service that orchestrates session lifecycle."""

    def __init__(self, storage: S3Storage) -> None:
        self._storage = storage

    async def create_session(self, request: SessionCreateRequest) -> Session:
        session = Session(
            project=request.project,
            metadata=request.metadata,
        )
        await self._storage.put_json(
            key=session.metadata_key,
            data=session.model_dump(mode="json"),
        )
        log.info(
            "session.created",
            session_id=str(session.session_id),
            project=session.project,
            key=session.metadata_key,
        )
        return session

    async def get_session(self, session_id: UUID, project: str) -> Session | None:
        # We need to list to find the session — we know the project but not the date.
        prefix = f"{project}/"
        matched_key = await self._storage.find_key_by_suffix(
            prefix=prefix, suffix=f"--{session_id}/metadata.json"
        )
        if matched_key is None:
            return None
        data = await self._storage.get_json(matched_key)
        if data is None:
            return None
        return Session.model_validate(data)

    async def get_or_create_session(
        self,
        session_id: UUID,
        project: str,
        metadata: dict | None = None,
    ) -> Session:
        """Return an existing session, or create one with the given identity.

        Idempotent: if a session with *session_id* already exists under
        *project*, it is returned as-is.  Otherwise a new session is
        persisted and returned.
        """
        existing = await self.get_session(session_id, project)
        if existing is not None:
            return existing

        session = Session(
            session_id=session_id,
            project=project,
            metadata=metadata,
        )
        await self._storage.put_json(
            key=session.metadata_key,
            data=session.model_dump(mode="json"),
        )
        log.info(
            "session.created",
            session_id=str(session.session_id),
            project=session.project,
            key=session.metadata_key,
        )
        return session


# ---------------------------------------------------------------------------
# Factory — wires up storage from env vars
# ---------------------------------------------------------------------------


def make_session_service() -> SessionService:
    """Build a SessionService from the current environment."""
    storage = S3Storage(
        endpoint_url=os.environ["ENDO_S3_ENDPOINT"],
        access_key=os.environ["ENDO_S3_ACCESS_KEY"],
        secret_key=os.environ["ENDO_S3_SECRET_KEY"],
        bucket=os.environ["ENDO_S3_BUCKET"],
        region=os.environ.get("ENDO_S3_REGION", "us-east-1"),
    )
    return SessionService(storage=storage)
