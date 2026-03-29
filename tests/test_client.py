"""Unit tests for endoscope.client.EndoscopeAPIClient.

All HTTP calls are mocked via unittest.mock.patch on httpx.request / httpx.get.
No network access required.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from endoscope.client import EndoscopeAPIClient, EndoscopeAPIError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(
    status_code: int = 200,
    json_data: object | None = None,
    content: bytes = b"",
    text: str = "",
) -> MagicMock:
    """Build a MagicMock that quacks like an httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text  # callers that need text should pass it explicitly
    resp.content = content
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("no json body")
    return resp


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------

class TestListSessions:
    @patch("httpx.request")
    def test_returns_list(self, mock_request: MagicMock) -> None:
        data = [{"session_id": "abc", "project": "p"}]
        mock_request.return_value = _mock_response(json_data=data)

        client = EndoscopeAPIClient(base_url="http://test")
        result = client.list_sessions()

        assert result == data
        mock_request.assert_called_once()
        args, kwargs = mock_request.call_args
        assert args[0] == "GET"
        assert args[1] == "http://test/v1/sessions"

    @patch("httpx.request")
    def test_returns_empty_list(self, mock_request: MagicMock) -> None:
        mock_request.return_value = _mock_response(json_data=[])

        client = EndoscopeAPIClient()
        assert client.list_sessions() == []


# ---------------------------------------------------------------------------
# get_session
# ---------------------------------------------------------------------------

class TestGetSession:
    @patch("httpx.request")
    def test_returns_dict(self, mock_request: MagicMock) -> None:
        data = {"session_id": "abc", "files": ["f1.txt"]}
        mock_request.return_value = _mock_response(json_data=data)

        client = EndoscopeAPIClient(base_url="http://test")
        result = client.get_session("abc")

        assert result == data
        args, _ = mock_request.call_args
        assert args[1] == "http://test/v1/sessions/abc"


# ---------------------------------------------------------------------------
# delete_session
# ---------------------------------------------------------------------------

class TestDeleteSession:
    @patch("httpx.request")
    def test_returns_dict(self, mock_request: MagicMock) -> None:
        data = {"deleted": "abc"}
        mock_request.return_value = _mock_response(json_data=data)

        client = EndoscopeAPIClient()
        result = client.delete_session("abc")

        assert result == data
        args, _ = mock_request.call_args
        assert args[0] == "DELETE"


# ---------------------------------------------------------------------------
# prune_sessions
# ---------------------------------------------------------------------------

class TestPruneSessions:
    @patch("httpx.request")
    def test_prune_by_age(self, mock_request: MagicMock) -> None:
        data = {"pruned": 3}
        mock_request.return_value = _mock_response(json_data=data)

        client = EndoscopeAPIClient()
        result = client.prune_sessions(older_than="7d")

        assert result == data
        _, kwargs = mock_request.call_args
        assert kwargs["json"] == {"older_than": "7d"}

    @patch("httpx.request")
    def test_prune_all(self, mock_request: MagicMock) -> None:
        data = {"pruned": 10}
        mock_request.return_value = _mock_response(json_data=data)

        client = EndoscopeAPIClient()
        result = client.prune_sessions(all=True)

        assert result == data
        _, kwargs = mock_request.call_args
        assert kwargs["json"] == {"all": True}


# ---------------------------------------------------------------------------
# download_file
# ---------------------------------------------------------------------------

class TestDownloadFile:
    @patch("httpx.request")
    def test_returns_bytes(self, mock_request: MagicMock) -> None:
        raw = b"\x89PNG\r\n\x1a\nfiledata"
        mock_request.return_value = _mock_response(content=raw, status_code=200)

        client = EndoscopeAPIClient(base_url="http://test")
        result = client.download_file("s1", "img.png")

        assert result == raw
        args, kwargs = mock_request.call_args
        assert args[1] == "http://test/v1/sessions/s1/files/img.png"


# ---------------------------------------------------------------------------
# pull_session
# ---------------------------------------------------------------------------

class TestPullSession:
    def test_creates_dir_and_files(self, tmp_path: Path) -> None:
        client = EndoscopeAPIClient(base_url="http://test")

        session_data = {
            "session_id": "sess1",
            "files": ["report.txt", "data.csv"],
        }
        file_contents = {
            "report.txt": b"hello report",
            "data.csv": b"a,b\n1,2",
        }

        with patch.object(client, "get_session", return_value=session_data):
            with patch.object(
                client, "download_file", side_effect=lambda sid, f: file_contents[f]
            ):
                result = client.pull_session("sess1", tmp_path)

        assert result == tmp_path / "sess1"
        assert result.is_dir()

        # metadata.json
        meta = json.loads((result / "metadata.json").read_text())
        assert meta == session_data

        # downloaded files
        assert (result / "report.txt").read_bytes() == b"hello report"
        assert (result / "data.csv").read_bytes() == b"a,b\n1,2"

    def test_pull_session_no_files(self, tmp_path: Path) -> None:
        client = EndoscopeAPIClient()
        session_data = {"session_id": "empty", "files": []}

        with patch.object(client, "get_session", return_value=session_data):
            result = client.pull_session("empty", tmp_path)

        assert result.is_dir()
        meta = json.loads((result / "metadata.json").read_text())
        assert meta["session_id"] == "empty"
        # No extra files besides metadata
        assert list(result.iterdir()) == [result / "metadata.json"]

    def test_path_traversal_sanitized(self, tmp_path: Path) -> None:
        """Filenames with path components are stripped to their basename."""
        client = EndoscopeAPIClient(base_url="http://test")

        session_data = {
            "session_id": "sess-traversal",
            "files": ["../evil.sh", "sub/nested.txt", "normal.txt"],
        }
        file_contents = {
            "../evil.sh": b"evil",
            "sub/nested.txt": b"nested",
            "normal.txt": b"normal",
        }

        with patch.object(client, "get_session", return_value=session_data):
            with patch.object(
                client,
                "download_file",
                side_effect=lambda sid, f: file_contents[f],
            ):
                result = client.pull_session("sess-traversal", tmp_path)

        # Files written with basename only — no directory escape
        assert (result / "evil.sh").read_bytes() == b"evil"
        assert (result / "nested.txt").read_bytes() == b"nested"
        assert (result / "normal.txt").read_bytes() == b"normal"
        # No file written outside the session dir
        assert not (tmp_path / "evil.sh").exists()

# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestAPIError:
    @patch("httpx.request")
    def test_4xx_raises_with_json_detail(self, mock_request: MagicMock) -> None:
        mock_request.return_value = _mock_response(
            status_code=404,
            json_data={"error": "session not found"},
            text='{"error": "session not found"}',
        )

        client = EndoscopeAPIClient()
        with pytest.raises(EndoscopeAPIError) as exc_info:
            client.get_session("missing")

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "session not found"

    @patch("httpx.request")
    def test_5xx_raises(self, mock_request: MagicMock) -> None:
        mock_request.return_value = _mock_response(
            status_code=500,
            text="Internal Server Error",
        )

        client = EndoscopeAPIClient()
        with pytest.raises(EndoscopeAPIError) as exc_info:
            client.list_sessions()

        assert exc_info.value.status_code == 500
        assert "Internal Server Error" in exc_info.value.detail

    @patch("httpx.request")
    def test_non_json_error_falls_back_to_text(self, mock_request: MagicMock) -> None:
        mock_request.return_value = _mock_response(
            status_code=502,
            text="<html>Bad Gateway</html>",
        )

        client = EndoscopeAPIClient()
        with pytest.raises(EndoscopeAPIError) as exc_info:
            client.delete_session("x")

        assert exc_info.value.status_code == 502
        assert "Bad Gateway" in exc_info.value.detail

    @patch("httpx.request")
    def test_download_file_error(self, mock_request: MagicMock) -> None:
        mock_request.return_value = _mock_response(
            status_code=403,
            json_data={"error": "forbidden"},
            text="forbidden",
        )

        client = EndoscopeAPIClient()
        with pytest.raises(EndoscopeAPIError) as exc_info:
            client.download_file("s1", "secret.txt")

        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# API key header handling
# ---------------------------------------------------------------------------

class TestAPIKeyHeader:
    @patch("httpx.request")
    def test_header_included_when_set(self, mock_request: MagicMock) -> None:
        mock_request.return_value = _mock_response(json_data=[])

        client = EndoscopeAPIClient(base_url="http://test", api_key="secret123")
        client.list_sessions()

        _, kwargs = mock_request.call_args
        assert kwargs["headers"]["x-api-key"] == "secret123"

    @patch("httpx.request")
    def test_header_omitted_when_empty(self, mock_request: MagicMock) -> None:
        mock_request.return_value = _mock_response(json_data=[])

        client = EndoscopeAPIClient(base_url="http://test", api_key="")
        client.list_sessions()

        _, kwargs = mock_request.call_args
        assert "x-api-key" not in kwargs["headers"]
