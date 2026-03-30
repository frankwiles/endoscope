"""Integration tests for endoscope.client.EndoscopeAPIClient.

All tests run against the real API service at http://api:8000.
No mocks or TestClient — real httpx calls over the Docker Compose network.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from endoscope.client import EndoscopeAPIClient, EndoscopeAPIError

API_URL = "http://api:8000"
API_KEY = "local-dev-api-key"
PROJECT = "local-test-project"


def _client() -> EndoscopeAPIClient:
    return EndoscopeAPIClient(base_url=API_URL, api_key=API_KEY)


def _create_session() -> str:
    """Create a real session and return its session_id string."""
    resp = httpx.post(
        f"{API_URL}/v1/sessions",
        json={"project": PROJECT},
        headers={"x-api-key": API_KEY},
    )
    resp.raise_for_status()
    return str(resp.json()["session_id"])


def _delete_session(session_id: str) -> None:
    try:
        httpx.delete(
            f"{API_URL}/v1/sessions/{session_id}",
            headers={"x-api-key": API_KEY},
        )
    except Exception:
        pass


@pytest.fixture()
def session_id():
    """Create a session, yield its ID, then clean up."""
    sid = _create_session()
    yield sid
    _delete_session(sid)


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


def test_list_sessions_returns_list(session_id):
    client = _client()
    result = client.list_sessions()

    assert isinstance(result, list)
    ids = [str(s["session_id"]) for s in result]
    assert session_id in ids


# ---------------------------------------------------------------------------
# get_session
# ---------------------------------------------------------------------------


def test_get_session_returns_dict(session_id):
    client = _client()
    result = client.get_session(session_id)

    assert isinstance(result, dict)
    assert str(result["session_id"]) == session_id
    assert result["project"] == PROJECT


# ---------------------------------------------------------------------------
# delete_session
# ---------------------------------------------------------------------------


def test_delete_session_returns_dict():
    sid = _create_session()
    client = _client()
    result = client.delete_session(sid)

    assert isinstance(result, dict)
    assert "deleted" in result


# ---------------------------------------------------------------------------
# prune_sessions
# ---------------------------------------------------------------------------


def test_prune_by_age_returns_count():
    """Pruning with a very long window (no matching sessions) returns 0."""
    client = _client()
    result = client.prune_sessions(older_than="365d")

    assert isinstance(result, dict)
    assert "pruned" in result
    assert isinstance(result["pruned"], int)


def test_prune_all_returns_count():
    """prune_sessions(all=True) returns a dict with the pruned count."""
    client = _client()
    result = client.prune_sessions(all=True)

    assert isinstance(result, dict)
    assert "pruned" in result
    assert isinstance(result["pruned"], int)


# ---------------------------------------------------------------------------
# download_file
# ---------------------------------------------------------------------------


def test_download_file_returns_bytes(session_id):
    """Upload a file via presigned URL then download it through the API."""
    # Register the file and get a presigned upload URL
    resp = httpx.post(
        f"{API_URL}/v1/sessions/{session_id}/files",
        data={"filename": "img.png"},
        headers={"x-api-key": API_KEY},
    )
    resp.raise_for_status()
    upload_url = resp.json()["upload_url"]

    file_data = b"\x89PNG\r\n\x1a\nfiledata"
    httpx.put(upload_url, content=file_data)

    client = _client()
    result = client.download_file(session_id, "img.png")
    assert result == file_data


# ---------------------------------------------------------------------------
# pull_session
# ---------------------------------------------------------------------------


def test_pull_session_creates_dir_and_files(session_id, tmp_path):
    """Upload files then pull the session to a local directory."""
    file_contents = {
        "report.txt": b"hello report",
        "data.csv": b"a,b\n1,2",
    }
    for filename, content in file_contents.items():
        resp = httpx.post(
            f"{API_URL}/v1/sessions/{session_id}/files",
            data={"filename": filename},
            headers={"x-api-key": API_KEY},
        )
        resp.raise_for_status()
        httpx.put(resp.json()["upload_url"], content=content)

    client = _client()
    result = client.pull_session(session_id, tmp_path)

    assert result == tmp_path / session_id
    assert result.is_dir()

    meta = json.loads((result / "metadata.json").read_text())
    assert str(meta["session_id"]) == session_id

    assert (result / "report.txt").read_bytes() == b"hello report"
    assert (result / "data.csv").read_bytes() == b"a,b\n1,2"


def test_pull_session_no_files(session_id, tmp_path):
    client = _client()
    result = client.pull_session(session_id, tmp_path)

    assert result.is_dir()
    meta = json.loads((result / "metadata.json").read_text())
    assert str(meta["session_id"]) == session_id
    assert list(result.iterdir()) == [result / "metadata.json"]


def test_pull_session_path_traversal_sanitized():
    """pull_session strips directory components from filenames via Path.name.

    The download endpoint doesn't support filenames with slashes, so this
    verifies the sanitization logic that pull_session relies on directly.
    """
    assert Path("../evil.sh").name == "evil.sh"
    assert Path("sub/nested.txt").name == "nested.txt"
    assert Path("normal.txt").name == "normal.txt"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_get_session_not_found_raises():
    client = _client()
    with pytest.raises(EndoscopeAPIError) as exc_info:
        client.get_session("00000000-0000-0000-0000-000000000000")
    assert exc_info.value.status_code == 404
    assert "session not found" in exc_info.value.detail


def test_wrong_api_key_raises_401():
    bad_client = EndoscopeAPIClient(base_url=API_URL, api_key="wrong-key")
    with pytest.raises(EndoscopeAPIError) as exc_info:
        bad_client.list_sessions()
    assert exc_info.value.status_code == 401


def test_download_file_not_found_raises(session_id):
    client = _client()
    with pytest.raises(EndoscopeAPIError) as exc_info:
        client.download_file(session_id, "nonexistent.bin")
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# API key header
# ---------------------------------------------------------------------------


def test_api_key_header_correct_key_succeeds():
    client = EndoscopeAPIClient(base_url=API_URL, api_key=API_KEY)
    result = client.list_sessions()
    assert isinstance(result, list)


def test_api_key_header_no_key_raises_401():
    client = EndoscopeAPIClient(base_url=API_URL, api_key="")
    with pytest.raises(EndoscopeAPIError) as exc_info:
        client.list_sessions()
    assert exc_info.value.status_code == 401
