"""Integration tests for the session service layer.

All tests run against real S3Storage backed by the Docker Compose RustFS
instance.  Each test gets an isolated project name (UUID-based) so data
never collides between tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest

from endoscope.services import (
    Session,
    SessionCreateRequest,
    SessionService,
    parse_session_key,
)
from endoscope.storage import S3Storage


def _make_session(**overrides) -> Session:
    defaults = {
        "session_id": UUID("12345678-1234-1234-1234-123456789abc"),
        "timestamp": datetime(2026, 3, 28, 12, 0, 0, tzinfo=UTC),
        "project": "test-project",
        "metadata": None,
        "events": [],
        "files": [],
    }
    defaults.update(overrides)
    return Session(
        session_id=defaults["session_id"],
        timestamp=defaults["timestamp"],
        project=defaults["project"],
        metadata=defaults["metadata"],
        events=defaults["events"],
        files=defaults["files"],
    )


# ---------------------------------------------------------------------------
# Session domain model (pure unit tests — no storage needed)
# ---------------------------------------------------------------------------


def test_storage_key_format():
    s = _make_session()
    assert s.storage_prefix == "test-project/2026/03/28/20260328T120000Z--12345678-1234-1234-1234-123456789abc"
    assert s.metadata_key.endswith("/metadata.json")


def test_session_create_request_validation():
    req = SessionCreateRequest(project="foo", metadata={"env": "staging"})
    assert req.project == "foo"
    assert req.metadata == {"env": "staging"}


def test_session_create_request_minimal():
    req = SessionCreateRequest(project="bar")
    assert req.metadata is None


def test_session_defaults():
    s = Session(project="baz")
    assert s.session_id is not None
    assert s.timestamp.tzinfo is not None


def test_session_serialization_roundtrip():
    s = _make_session(metadata={"key": "value"})
    data = s.model_dump(mode="json")
    restored = Session.model_validate(data)
    assert restored == s


# ---------------------------------------------------------------------------
# SessionService.create_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_writes_metadata_to_storage(svc: SessionService, project: str):
    req = SessionCreateRequest(project=project, metadata={"tier": "prod"})
    session = await svc.create_session(req)

    assert session.project == project
    assert session.metadata == {"tier": "prod"}

    # Verify it was actually written to S3
    fetched = await svc.get_session(session.session_id, project)
    assert fetched is not None
    assert fetched.session_id == session.session_id
    assert fetched.metadata == {"tier": "prod"}


@pytest.mark.asyncio
async def test_create_session_without_metadata(svc: SessionService, project: str):
    session = await svc.create_session(SessionCreateRequest(project=project))
    assert session.metadata is None

    fetched = await svc.get_session(session.session_id, project)
    assert fetched is not None
    assert fetched.metadata is None


# ---------------------------------------------------------------------------
# SessionService.get_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_session_found(svc: SessionService, project: str):
    created = await svc.create_session(SessionCreateRequest(project=project))
    result = await svc.get_session(created.session_id, project)

    assert result is not None
    assert result.session_id == created.session_id
    assert result.project == project


@pytest.mark.asyncio
async def test_get_session_not_found(svc: SessionService, project: str):
    result = await svc.get_session(UUID("00000000-0000-0000-0000-000000000000"), project)
    assert result is None


# ---------------------------------------------------------------------------
# SessionService.list_sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sessions_sorted_most_recent_first(svc: SessionService, storage: S3Storage, project: str):
    """list_sessions returns SessionSummary objects sorted most-recent first
    with accurate event and file counts. Stray keys are ignored."""
    # Write two sessions with controlled timestamps directly to S3
    session_a = Session(
        project=project,
        timestamp=datetime(2026, 3, 28, 12, 0, 0, tzinfo=UTC),
        events=[{"type": "log"}, {"type": "error"}],
        files=["dump.bin"],
    )
    session_b = Session(
        project=project,
        timestamp=datetime(2026, 3, 29, 8, 30, 0, tzinfo=UTC),
    )
    await storage.put_json(key=session_a.metadata_key, data=session_a.model_dump(mode="json"))
    await storage.put_json(key=session_b.metadata_key, data=session_b.model_dump(mode="json"))

    # Write a stray key that doesn't match the metadata.json pattern — should be ignored
    await storage.put_json(key=f"{project}/2026/03/28/stray-file.txt", data={})
    # Write a key that ends in /metadata.json but has a non-parseable path
    await storage.put_json(key=f"{project}/bad-path/metadata.json", data={})

    summaries = await svc.list_sessions(project)

    assert len(summaries) == 2
    # Most recent first
    assert summaries[0].session_id == session_b.session_id
    assert summaries[0].timestamp == datetime(2026, 3, 29, 8, 30, 0, tzinfo=UTC)
    assert summaries[0].event_count == 0
    assert summaries[0].file_count == 0

    assert summaries[1].session_id == session_a.session_id
    assert summaries[1].timestamp == datetime(2026, 3, 28, 12, 0, 0, tzinfo=UTC)
    assert summaries[1].event_count == 2
    assert summaries[1].file_count == 1


# ---------------------------------------------------------------------------
# SessionService.delete_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_session_removes_all_objects(svc: SessionService, storage: S3Storage, project: str):
    """delete_session removes all S3 objects under the session prefix."""
    session = await svc.create_session(SessionCreateRequest(project=project))

    # Register a file and upload bytes so there's more than just metadata.json
    result = await svc.add_file(session.session_id, project, "dump.bin")
    assert result is not None
    _, upload_url = result
    httpx.put(upload_url, content=b"\x00\x01\x02\x03")

    # Delete the session
    deleted = await svc.delete_session(session.session_id, project)
    assert deleted is True

    # Verify all objects under the session prefix are gone
    remaining_keys = await storage.list_keys(prefix=f"{session.storage_prefix}/")
    assert remaining_keys == []

    # Verify session is no longer findable
    found = await svc.get_session(session.session_id, project)
    assert found is None


@pytest.mark.asyncio
async def test_delete_session_returns_false_when_not_found(svc: SessionService, project: str):
    deleted = await svc.delete_session(UUID("00000000-0000-0000-0000-000000000000"), project)
    assert deleted is False


# ---------------------------------------------------------------------------
# SessionService.prune_sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prune_sessions_by_age(svc: SessionService, storage: S3Storage, project: str):
    """prune_sessions with older_than only deletes sessions past the cutoff."""
    # Write an old session (20 days ago) directly to S3
    old_session = Session(
        project=project,
        timestamp=datetime.now(UTC) - timedelta(days=20),
    )
    await storage.put_json(key=old_session.metadata_key, data=old_session.model_dump(mode="json"))

    # Create a recent session via the service
    recent_session = await svc.create_session(SessionCreateRequest(project=project))

    count = await svc.prune_sessions(project, older_than=timedelta(days=7))

    assert count == 1

    # Old session should be gone, recent session should remain
    remaining = await svc.list_sessions(project)
    assert len(remaining) == 1
    assert remaining[0].session_id == recent_session.session_id


@pytest.mark.asyncio
async def test_prune_all_sessions(svc: SessionService, storage: S3Storage, project: str):
    """prune_sessions with all=True deletes every session for the project."""
    session_a = Session(project=project, timestamp=datetime(2026, 3, 27, 10, 0, 0, tzinfo=UTC))
    session_b = Session(project=project, timestamp=datetime(2026, 3, 28, 10, 0, 0, tzinfo=UTC))
    await storage.put_json(key=session_a.metadata_key, data=session_a.model_dump(mode="json"))
    await storage.put_json(key=session_b.metadata_key, data=session_b.model_dump(mode="json"))

    count = await svc.prune_sessions(project, all=True)

    assert count == 2
    remaining = await svc.list_sessions(project)
    assert len(remaining) == 0


# ---------------------------------------------------------------------------
# SessionService.get_file_bytes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_file_bytes_found(svc: SessionService, project: str):
    """get_file_bytes returns bytes when the file has been uploaded."""
    session = await svc.create_session(SessionCreateRequest(project=project))

    result = await svc.add_file(session.session_id, project, "dump.bin")
    assert result is not None
    _, upload_url = result

    file_bytes = b"\x00\x01\x02\x03"
    httpx.put(upload_url, content=file_bytes)

    fetched = await svc.get_file_bytes(session.session_id, project, "dump.bin")
    assert fetched == file_bytes


@pytest.mark.asyncio
async def test_get_file_bytes_not_in_session(svc: SessionService, project: str):
    """get_file_bytes returns None when the filename is not in session.files."""
    # Create session with "dump.bin" registered but not "nope.bin"
    session = await svc.create_session(SessionCreateRequest(project=project))
    await svc.add_file(session.session_id, project, "dump.bin")

    result = await svc.get_file_bytes(session.session_id, project, "nope.bin")
    assert result is None


@pytest.mark.asyncio
async def test_get_file_bytes_session_not_found(svc: SessionService, project: str):
    """get_file_bytes returns None when the session doesn't exist."""
    result = await svc.get_file_bytes(
        UUID("00000000-0000-0000-0000-000000000000"), project, "file.bin"
    )
    assert result is None


# ---------------------------------------------------------------------------
# Duplicate filename deduplication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_file_deduplicates_filename(svc: SessionService, project: str):
    """When the same filename already exists, add_file appends a random hash."""
    session = await svc.create_session(SessionCreateRequest(project=project))

    # Register "report.csv" once
    result1 = await svc.add_file(session.session_id, project, "report.csv")
    assert result1 is not None

    # Register "report.csv" again — should be deduplicated
    result2 = await svc.add_file(session.session_id, project, "report.csv")
    assert result2 is not None
    updated_session, _ = result2

    stored_name = updated_session.files[-1]
    assert stored_name != "report.csv"
    assert stored_name.startswith("report-")
    assert stored_name.endswith(".csv")
    # Length: "report-" (7) + 4 hex chars + ".csv" (4) = 15
    assert len(stored_name) == 15


# ---------------------------------------------------------------------------
# Empty filename rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_file_rejects_empty_filename(svc: SessionService):
    with pytest.raises(ValueError, match="filename must not be empty"):
        await svc.add_file(UUID("12345678-1234-1234-1234-123456789abc"), "test-project", "")


@pytest.mark.asyncio
async def test_add_file_rejects_whitespace_filename(svc: SessionService):
    with pytest.raises(ValueError, match="filename must not be empty"):
        await svc.add_file(UUID("12345678-1234-1234-1234-123456789abc"), "test-project", "  ")


# ---------------------------------------------------------------------------
# Compact timestamp format and backward-compatible parsing
# ---------------------------------------------------------------------------


def test_parse_session_key_compact_timestamp():
    key = "my-proj/2026/03/28/20260328T120000Z--12345678-1234-1234-1234-123456789abc/metadata.json"
    result = parse_session_key(key)
    assert result is not None
    assert result["session_id"] == UUID("12345678-1234-1234-1234-123456789abc")
    assert result["timestamp"] == datetime(2026, 3, 28, 12, 0, 0, tzinfo=UTC)
    assert result["project"] == "my-proj"


def test_parse_session_key_legacy_timestamp():
    key = (
        "my-proj/2026/03/28/"
        "2026-03-28T12:00:00+00:00--12345678-1234-1234-1234-123456789abc/"
        "metadata.json"
    )
    result = parse_session_key(key)
    assert result is not None
    assert result["session_id"] == UUID("12345678-1234-1234-1234-123456789abc")
    assert result["timestamp"] == datetime(2026, 3, 28, 12, 0, 0, tzinfo=UTC)
    assert result["project"] == "my-proj"
