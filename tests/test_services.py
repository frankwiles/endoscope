"""Unit tests for the session service layer."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from endoscope.services import Session, SessionCreateRequest, SessionService


def _make_session(**overrides) -> Session:
    defaults = dict(
        session_id=UUID("12345678-1234-1234-1234-123456789abc"),
        timestamp=datetime(2026, 3, 28, 12, 0, 0, tzinfo=UTC),
        project="test-project",
        metadata=None,
    )
    defaults.update(overrides)
    return Session(**defaults)


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
# SessionService.get_or_create_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_returns_existing():
    stored = _make_session()
    storage = AsyncMock()
    storage.find_key_by_suffix.return_value = stored.metadata_key
    storage.get_json.return_value = stored.model_dump(mode="json")

    svc = SessionService(storage=storage)
    result = await svc.get_or_create_session(stored.session_id, "test-project")

    assert result.session_id == stored.session_id
    assert result.project == stored.project
    # Must NOT have written anything — session already existed.
    storage.put_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_or_create_creates_when_missing():
    storage = AsyncMock()
    storage.find_key_by_suffix.return_value = None

    svc = SessionService(storage=storage)
    sid = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    result = await svc.get_or_create_session(sid, "new-proj", metadata={"env": "dev"})

    assert result.session_id == sid
    assert result.project == "new-proj"
    assert result.metadata == {"env": "dev"}
    storage.put_json.assert_awaited_once()
    call_key = storage.put_json.call_args[1]["key"]
    assert call_key.startswith("new-proj/")
    assert call_key.endswith("/metadata.json")


@pytest.mark.asyncio
async def test_get_or_create_without_metadata():
    storage = AsyncMock()
    storage.find_key_by_suffix.return_value = None

    svc = SessionService(storage=storage)
    sid = UUID("11111111-2222-3333-4444-555555555555")
    result = await svc.get_or_create_session(sid, "proj")

    assert result.session_id == sid
    assert result.metadata is None
    data = storage.put_json.call_args[1]["data"]
    assert data["metadata"] is None
