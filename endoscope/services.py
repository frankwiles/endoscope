from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import structlog
from endoscope.storage import S3Storage
from pydantic import BaseModel, Field

from .config import EndoscopeConfig

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
    events: list[dict] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)

    @property
    def storage_prefix(self) -> str:
        """S3 key prefix:  <project>/yyyy/mm/dd/<compact-ts>--<session_id>/"""
        ts = self.timestamp
        date_part = ts.strftime("%Y/%m/%d")
        ts_compact = ts.strftime("%Y%m%dT%H%M%SZ")
        return f"{self.project}/{date_part}/{ts_compact}--{self.session_id}"

    @property
    def metadata_key(self) -> str:
        return f"{self.storage_prefix}/metadata.json"

    @property
    def files_prefix(self) -> str:
        return f"{self.storage_prefix}/files/"


class SessionSummary(BaseModel):
    """Lightweight session info for list views — no events or file contents."""

    session_id: UUID
    timestamp: datetime
    project: str
    event_count: int
    file_count: int


class PruneRequest(BaseModel):
    """Payload for prune operations."""

    older_than: str | None = None  # duration string like "7d", "24h"
    all: bool = False


# ---------------------------------------------------------------------------
# Key-path parsing
# ---------------------------------------------------------------------------

# Matches: <project>/yyyy/mm/dd/<iso-timestamp>--<uuid>/metadata.json
_KEY_PATTERN = re.compile(
    r"^(?P<project>[^/]+)/(?P<date>\d{4}/\d{2}/\d{2})/"
    r"(?P<ts>[^/]+)--(?P<uuid>[^/]+)/metadata\.json$"
)


def _parse_timestamp(ts_str: str) -> datetime:
    """Parse timestamp from a session key — handles both compact and ISO formats.

    Compact: 20260329T123456Z
    ISO:     2026-03-29T12:34:56+00:00
    """
    try:
        return datetime.strptime(ts_str, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return datetime.fromisoformat(ts_str)


def parse_session_key(key: str) -> dict | None:
    """Extract structured data from a metadata.json S3 key path.

    Returns dict with session_id, timestamp, project — or None if the key
    doesn't match the expected layout.
    """
    m = _KEY_PATTERN.match(key)
    if not m:
        return None
    try:
        return {
            "session_id": UUID(m.group("uuid")),
            "timestamp": _parse_timestamp(m.group("ts")),
            "project": m.group("project"),
        }
    except (ValueError, TypeError):
        return None


def parse_duration(s: str) -> timedelta:
    """Parse a simple duration string like '7d', '24h', '30m'.

    Supported suffixes: d (days), h (hours), m (minutes).
    """
    match = re.match(r"^(\d+)([dhm])$", s.strip().lower())
    if not match:
        raise ValueError(f"Invalid duration: {s!r}. Use e.g. '7d', '24h', '30m'.")
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "d":
        return timedelta(days=amount)
    elif unit == "h":
        return timedelta(hours=amount)
    else:
        return timedelta(minutes=amount)


def _dedup_filename(filename: str, existing: list[str]) -> str:
    """Return *filename* modified with a random suffix if it already appears in *existing*.

    Handles files with extensions: report.csv -> report-a1b2.csv.
    Handles files without extensions: README -> README-a1b2.
    """
    if filename not in existing:
        return filename
    base, sep, ext = filename.rpartition(".")
    while True:
        tag = secrets.token_hex(2)  # 4 hex characters
        candidate = f"{base}-{tag}.{ext}" if sep else f"{filename}-{tag}"
        if candidate not in existing:
            return candidate

# ---------------------------------------------------------------------------
# Application service (DDD)
# ---------------------------------------------------------------------------


class SessionService:
    """Domain-driven service that orchestrates session lifecycle."""

    def __init__(self, storage: S3Storage) -> None:
        self._storage = storage

    async def check_ready(self) -> bool:
        """Check if the backing S3 storage is reachable."""
        return await self._storage.check_ready()


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

    async def get_session(
        self, session_id: UUID, project: str | None = None
    ) -> Session | None:
        """Retrieve a session by ID, optionally scoped to a project.

        When *project* is given, only searches under that project prefix.
        Otherwise searches the entire bucket (slower but works cross-project).
        """
        prefix = f"{project}/" if project else ""
        matched_key = await self._storage.find_key_by_suffix(
            prefix=prefix,
            suffix=f"--{session_id}/metadata.json",
        )
        if matched_key is None:
            return None
        data = await self._storage.get_json(matched_key)
        if data is None:
            return None
        return Session.model_validate(data)

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def add_event(
        self, session_id: UUID, project: str | None = None, event: dict = None
    ) -> Session | None:
        """Append an event to an existing session. Returns updated session or None."""
        session = await self.get_session(session_id, project)
        if session is None:
            return None
        session.events.append(event)
        await self._storage.put_json(
            key=session.metadata_key,
            data=session.model_dump(mode="json"),
        )
        log.debug(
            "session.event_added",
            session_id=str(session_id),
            event_count=len(session.events),
        )
        return session

    async def add_file(
        self, session_id: UUID, project: str | None = None, filename: str = ""
    ) -> tuple[Session, str] | None:
        """Register a file with a session and return (session, presigned_url).

        Returns None if session not found.
        Raises ValueError if filename is empty.
        """
        if not filename or not filename.strip():
            raise ValueError("filename must not be empty")
        session = await self.get_session(session_id, project)
        if session is None:
            return None
        filename = _dedup_filename(filename, session.files)
        s3_key = f"{session.files_prefix}{filename}"
        url = await self._storage.generate_presigned_url(s3_key)
        session.files.append(filename)
        await self._storage.put_json(
            key=session.metadata_key,
            data=session.model_dump(mode="json"),
        )
        log.debug(
            "session.file_added",
            session_id=str(session_id),
            filename=filename,
        )
        return session, url

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def list_sessions(self, project: str) -> list[SessionSummary]:
        """List all sessions for a project, most recent first.

        Parses session_id and timestamp from key paths, then fetches
        each metadata.json for accurate event and file counts.
        """
        keys = await self._storage.list_keys(prefix=f"{project}/")
        metadata_keys = [k for k in keys if k.endswith("/metadata.json")]

        summaries: list[SessionSummary] = []
        for key in metadata_keys:
            parsed = parse_session_key(key)
            if parsed is None:
                continue
            # Fetch metadata for accurate counts
            data = await self._storage.get_json(key)
            if data is None:
                continue
            summaries.append(
                SessionSummary(
                    session_id=parsed["session_id"],
                    timestamp=parsed["timestamp"],
                    project=parsed["project"],
                    event_count=len(data.get("events", [])),
                    file_count=len(data.get("files", [])),
                )
            )

        summaries.sort(key=lambda s: s.timestamp, reverse=True)
        return summaries

    async def get_file_bytes(
        self, session_id: UUID, project: str | None = None, filename: str = ""
    ) -> bytes | None:
        """Download raw bytes for a file attached to a session."""
        session = await self.get_session(session_id, project)
        if session is None:
            return None
        if filename not in session.files:
            return None
        s3_key = f"{session.files_prefix}{filename}"
        return await self._storage.get_object_bytes(s3_key)

    # ------------------------------------------------------------------
    # Delete operations
    # ------------------------------------------------------------------

    async def delete_session(self, session_id: UUID, project: str | None = None) -> bool:
        """Delete a session and all associated S3 objects.

        Returns True if the session was found and deleted, False otherwise.
        """
        session = await self.get_session(session_id, project)
        if session is None:
            return False
        keys = await self._storage.list_keys(prefix=f"{session.storage_prefix}/")
        if keys:
            await self._storage.delete_objects(keys)
        log.info(
            "session.deleted",
            session_id=str(session_id),
            objects_deleted=len(keys),
        )
        return True

    async def prune_sessions(
        self,
        project: str,
        older_than: timedelta | None = None,
        all: bool = False,
    ) -> int:
        """Bulk delete sessions, optionally filtered by age.

        Returns the count of pruned sessions.
        """
        keys = await self._storage.list_keys(prefix=f"{project}/")
        metadata_keys = [k for k in keys if k.endswith("/metadata.json")]

        now = datetime.now(UTC)
        cutoff = now - older_than if older_than else None

        prefixes_to_delete: list[str] = []
        for key in metadata_keys:
            parsed = parse_session_key(key)
            if parsed is None:
                continue
            if all or (cutoff and parsed["timestamp"] < cutoff):
                # Extract the session prefix (everything before /metadata.json)
                prefix = key.rsplit("/metadata.json", 1)[0]
                prefixes_to_delete.append(prefix)

        # Collect all object keys under the matched prefixes
        all_keys: list[str] = []
        for prefix in prefixes_to_delete:
            prefix_keys = await self._storage.list_keys(prefix=f"{prefix}/")
            all_keys.extend(prefix_keys)

        if all_keys:
            await self._storage.delete_objects(all_keys)

        log.info(
            "session.prune",
            project=project,
            sessions_pruned=len(prefixes_to_delete),
            objects_deleted=len(all_keys),
        )
        return len(prefixes_to_delete)


# ---------------------------------------------------------------------------
# Factory — wires up storage from config
# ---------------------------------------------------------------------------


def make_session_service(cfg: EndoscopeConfig) -> SessionService:
    """Build a SessionService from a configuration object."""
    storage = S3Storage(
        endpoint_url=cfg.s3_endpoint,
        access_key=cfg.s3_access_key,
        secret_key=cfg.s3_secret_key,
        bucket=cfg.s3_bucket,
        region=cfg.s3_region,
    )
    return SessionService(storage=storage)
