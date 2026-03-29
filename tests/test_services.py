"""Unit tests for the session service layer."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from endoscope.services import Session, SessionCreateRequest, SessionService


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
# Session domain model
# ---------------------------------------------------------------------------


def test_storage_key_format():
    s = _make_session()
    assert s.storage_prefix == "test-project/2026/03/28/2026-03-28T12:00:00+00:00--12345678-1234-1234-1234-123456789abc"
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
async def test_create_session_writes_metadata_to_storage():
    storage = AsyncMock()
    svc = SessionService(storage=storage)

    req = SessionCreateRequest(project="my-proj", metadata={"tier": "prod"})
    session = await svc.create_session(req)

    assert session.project == "my-proj"
    assert session.metadata == {"tier": "prod"}
    storage.put_json.assert_awaited_once()
    call_key = storage.put_json.call_args[1]["key"]
    assert call_key.startswith("my-proj/")
    assert call_key.endswith("/metadata.json")


@pytest.mark.asyncio
async def test_create_session_without_metadata():
    storage = AsyncMock()
    svc = SessionService(storage=storage)

    session = await svc.create_session(SessionCreateRequest(project="x"))
    assert session.metadata is None
    data = storage.put_json.call_args[1]["data"]
    assert data["metadata"] is None


# ---------------------------------------------------------------------------
# SessionService.get_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_session_found():
    stored = _make_session()
    storage = AsyncMock()
    storage.find_key_by_suffix.return_value = stored.metadata_key
    storage.get_json.return_value = stored.model_dump(mode="json")

    svc = SessionService(storage=storage)
    result = await svc.get_session(stored.session_id, "test-project")

    assert result is not None
    assert result.session_id == stored.session_id


@pytest.mark.asyncio
async def test_get_session_not_found():
    storage = AsyncMock()
    storage.find_key_by_suffix.return_value = None

    svc = SessionService(storage=storage)
    result = await svc.get_session(UUID("00000000-0000-0000-0000-000000000000"), "nope")
    assert result is None



# ---------------------------------------------------------------------------
# SessionService.list_sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sessions_parses_key_paths():
    """list_sessions parses S3 key paths and returns SessionSummary objects."""
    storage = AsyncMock()
    svc = SessionService(storage=storage)

    # Two valid metadata keys with different dates
    key_a = (
        "my-proj/2026/03/28/"
        "2026-03-28T12:00:00+00:00--12345678-1234-1234-1234-123456789abc/"
        "metadata.json"
    )
    key_b = (
        "my-proj/2026/03/29/"
        "2026-03-29T08:30:00+00:00--aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/"
        "metadata.json"
    )
    # A key that does NOT match the expected pattern
    key_bad = "my-proj/2026/03/28/stray-file.txt"

    storage.list_keys.return_value = [key_a, key_b, key_bad]

    def _get_json(key):
        if key == key_a:
            return {
                "session_id": "12345678-1234-1234-1234-123456789abc",
                "timestamp": "2026-03-28T12:00:00+00:00",
                "project": "my-proj",
                "events": [{"type": "log"}, {"type": "error"}],
                "files": ["dump.bin"],
            }
        if key == key_b:
            return {
                "session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "timestamp": "2026-03-29T08:30:00+00:00",
                "project": "my-proj",
                "events": [],
                "files": [],
            }
        return None

    storage.get_json.side_effect = _get_json

    summaries = await svc.list_sessions("my-proj")

    # Most recent first
    assert len(summaries) == 2
    assert summaries[0].session_id == UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert summaries[0].timestamp == datetime(2026, 3, 29, 8, 30, 0, tzinfo=UTC)
    assert summaries[0].event_count == 0
    assert summaries[0].file_count == 0

    assert summaries[1].session_id == UUID("12345678-1234-1234-1234-123456789abc")
    assert summaries[1].timestamp == datetime(2026, 3, 28, 12, 0, 0, tzinfo=UTC)
    assert summaries[1].event_count == 2
    assert summaries[1].file_count == 1


# ---------------------------------------------------------------------------
# SessionService.delete_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_session_removes_all_objects():
    """delete_session collects all keys under the session prefix and deletes them."""
    storage = AsyncMock()
    svc = SessionService(storage=storage)

    session = _make_session()
    # get_session will be called internally — wire find_key_by_suffix + get_json
    storage.find_key_by_suffix.return_value = session.metadata_key
    storage.get_json.return_value = session.model_dump(mode="json")

    object_keys = [
        f"{session.storage_prefix}/metadata.json",
        f"{session.storage_prefix}/files/dump.bin",
        f"{session.storage_prefix}/events/0.json",
    ]
    storage.list_keys.return_value = object_keys

    deleted = await svc.delete_session(session.session_id, "test-project")

    assert deleted is True
    storage.delete_objects.assert_awaited_once_with(object_keys)


@pytest.mark.asyncio
async def test_delete_session_returns_false_when_not_found():
    """delete_session returns False when the session doesn't exist."""
    storage = AsyncMock()
    storage.find_key_by_suffix.return_value = None
    svc = SessionService(storage=storage)

    deleted = await svc.delete_session(
        UUID("00000000-0000-0000-0000-000000000000"), "nope"
    )
    assert deleted is False
    storage.delete_objects.assert_not_awaited()


# ---------------------------------------------------------------------------
# SessionService.prune_sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prune_sessions_by_age():
    """prune_sessions with older_than only deletes sessions past the cutoff."""
    storage = AsyncMock()
    svc = SessionService(storage=storage)

    # One old session (20 days ago) and one recent (1 day ago)
    old_ts = datetime(2026, 3, 9, 10, 0, 0, tzinfo=UTC)
    recent_ts = datetime(2026, 3, 28, 10, 0, 0, tzinfo=UTC)
    old_uuid = UUID("11111111-0000-0000-0000-000000000000")
    recent_uuid = UUID("22222222-0000-0000-0000-000000000000")

    old_key = (
        f"my-proj/{old_ts:%Y/%m/%d}/{old_ts.isoformat()}--{old_uuid}/metadata.json"
    )
    recent_key = (
        f"my-proj/{recent_ts:%Y/%m/%d}/{recent_ts.isoformat()}--{recent_uuid}/metadata.json"
    )

    storage.list_keys.return_value = [old_key, recent_key]

    # When prune collects keys under the old prefix, return some objects
    old_prefix = old_key.rsplit("/metadata.json", 1)[0]
    old_objects = [
        f"{old_prefix}/metadata.json",
        f"{old_prefix}/files/heap.bin",
    ]

    # list_keys is called twice: first for discovery, then per-prefix.
    # First call returns metadata keys, second returns objects under old prefix.
    storage.list_keys.side_effect = [[old_key, recent_key], old_objects]

    count = await svc.prune_sessions("my-proj", older_than=timedelta(days=7))

    assert count == 1
    storage.delete_objects.assert_awaited_once_with(old_objects)


@pytest.mark.asyncio
async def test_prune_all_sessions():
    """prune_sessions with all=True deletes every session for the project."""
    storage = AsyncMock()
    svc = SessionService(storage=storage)

    ts_a = datetime(2026, 3, 27, 10, 0, 0, tzinfo=UTC)
    ts_b = datetime(2026, 3, 28, 10, 0, 0, tzinfo=UTC)
    uuid_a = UUID("aaaaaaaa-0000-0000-0000-000000000000")
    uuid_b = UUID("bbbbbbbb-0000-0000-0000-000000000000")

    key_a = f"my-proj/{ts_a:%Y/%m/%d}/{ts_a.isoformat()}--{uuid_a}/metadata.json"
    key_b = f"my-proj/{ts_b:%Y/%m/%d}/{ts_b.isoformat()}--{uuid_b}/metadata.json"

    prefix_a = key_a.rsplit("/metadata.json", 1)[0]
    prefix_b = key_b.rsplit("/metadata.json", 1)[0]

    objs_a = [f"{prefix_a}/metadata.json"]
    objs_b = [f"{prefix_b}/metadata.json", f"{prefix_b}/files/core.bin"]

    storage.list_keys.side_effect = [
        [key_a, key_b],  # discovery pass
        objs_a,            # prefix-a objects
        objs_b,            # prefix-b objects
    ]

    count = await svc.prune_sessions("my-proj", all=True)

    assert count == 2
    storage.delete_objects.assert_awaited_once_with(objs_a + objs_b)


# ---------------------------------------------------------------------------
# SessionService.get_file_bytes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_file_bytes_found():
    """get_file_bytes returns bytes when the file exists in the session."""
    storage = AsyncMock()
    svc = SessionService(storage=storage)

    session = _make_session(files=["dump.bin", "trace.log"])
    storage.find_key_by_suffix.return_value = session.metadata_key
    storage.get_json.return_value = session.model_dump(mode="json")
    storage.get_object_bytes.return_value = b"\x00\x01\x02\x03"

    result = await svc.get_file_bytes(session.session_id, "test-project", "dump.bin")

    assert result == b"\x00\x01\x02\x03"
    expected_key = f"{session.files_prefix}dump.bin"
    storage.get_object_bytes.assert_awaited_once_with(expected_key)


@pytest.mark.asyncio
async def test_get_file_bytes_not_in_session():
    """get_file_bytes returns None when the filename is not in session.files."""
    storage = AsyncMock()
    svc = SessionService(storage=storage)

    session = _make_session(files=["dump.bin"])
    storage.find_key_by_suffix.return_value = session.metadata_key
    storage.get_json.return_value = session.model_dump(mode="json")

    result = await svc.get_file_bytes(session.session_id, "test-project", "nope.bin")

    assert result is None
    storage.get_object_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_file_bytes_session_not_found():
    """get_file_bytes returns None when the session doesn't exist."""
    storage = AsyncMock()
    storage.find_key_by_suffix.return_value = None
    svc = SessionService(storage=storage)

    result = await svc.get_file_bytes(
        UUID("00000000-0000-0000-0000-000000000000"), "nope", "file.bin"
    )
    assert result is None
    storage.get_object_bytes.assert_not_awaited()
