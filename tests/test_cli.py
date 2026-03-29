"""Tests for the Typer CLI commands.

Uses typer.testing.CliRunner and mocks EndoscopeAPIClient so no real
network calls are made.

Note: Shared callback options (--api-key, --api-url, --project) must be
placed *before* the subcommand name in the argument list for this Typer
version, e.g.  ["--api-key", "k", "--project", "p", "list"].
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from endoscope.cli import app

runner = CliRunner()

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

SAMPLE_SESSIONS = [
    {
        "session_id": "abc-123",
        "timestamp": "2025-01-01T00:00:00Z",
        "event_count": 3,
        "file_count": 2,
    },
    {
        "session_id": "def-456",
        "timestamp": "2025-01-02T00:00:00Z",
        "event_count": 1,
        "file_count": 0,
    },
]

SAMPLE_SESSION_DETAIL = {
    "session_id": "abc-123",
    "project": "test-proj",
    "timestamp": "2025-01-01T00:00:00Z",
    "events": [{"type": "log", "message": "hello"}],
    "files": ["screenshot.png", "log.txt"],
}

# Shared options placed before the subcommand name
SHARED_OPTS = ["--api-key", "test-key", "--project", "test-proj"]


def _mock_client():
    """Return a fresh MagicMock standing in for EndoscopeAPIClient."""
    client = MagicMock()
    client.list_sessions.return_value = SAMPLE_SESSIONS
    client.get_session.return_value = SAMPLE_SESSION_DETAIL
    client.delete_session.return_value = {"deleted": True}
    client.pull_session.return_value = Path("/tmp/endoscope-out/abc-123")
    client.prune_sessions.return_value = {"pruned": 5}
    return client


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@patch("endoscope.cli.EndoscopeAPIClient")
def test_list_shows_table(MockClient):
    client = _mock_client()
    MockClient.return_value = client

    result = runner.invoke(app, [*SHARED_OPTS, "list"])
    assert result.exit_code == 0
    output = result.output
    assert "abc-123" in output
    assert "def-456" in output
    assert "Sessions" in output


@patch("endoscope.cli.EndoscopeAPIClient")
def test_list_json_flag(MockClient):
    client = _mock_client()
    MockClient.return_value = client

    result = runner.invoke(app, [*SHARED_OPTS, "list", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert len(parsed) == 2
    assert parsed[0]["session_id"] == "abc-123"


@patch("endoscope.cli.EndoscopeAPIClient")
def test_list_empty(MockClient):
    client = _mock_client()
    client.list_sessions.return_value = []
    MockClient.return_value = client

    result = runner.invoke(app, [*SHARED_OPTS, "list"])
    assert result.exit_code == 0
    assert "No sessions found" in result.output


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@patch("endoscope.cli.EndoscopeAPIClient")
def test_show_session_details(MockClient):
    client = _mock_client()
    MockClient.return_value = client

    result = runner.invoke(app, [*SHARED_OPTS, "show", "abc-123"])
    assert result.exit_code == 0
    output = result.output
    assert "abc-123" in output
    assert "test-proj" in output
    assert "Session abc-123" in output
    assert "screenshot.png" in output


@patch("endoscope.cli.EndoscopeAPIClient")
def test_show_json_flag(MockClient):
    client = _mock_client()
    MockClient.return_value = client

    result = runner.invoke(app, [*SHARED_OPTS, "show", "abc-123", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["session_id"] == "abc-123"
    assert parsed["project"] == "test-proj"


# ---------------------------------------------------------------------------
# pull
# ---------------------------------------------------------------------------


@patch("endoscope.cli.EndoscopeAPIClient")
def test_pull_single_session(MockClient):
    client = _mock_client()
    MockClient.return_value = client

    result = runner.invoke(app, [*SHARED_OPTS, "pull", "abc-123"])
    assert result.exit_code == 0
    client.pull_session.assert_called_once_with(
        "abc-123", Path("./endoscope-out/")
    )
    assert "Pulled" in result.output
    assert "abc-123" in result.output


@patch("endoscope.cli.EndoscopeAPIClient")
def test_pull_all_sessions(MockClient):
    client = _mock_client()
    MockClient.return_value = client

    result = runner.invoke(app, [*SHARED_OPTS, "pull", "--all"])
    assert result.exit_code == 0
    assert client.pull_session.call_count == len(SAMPLE_SESSIONS)


@patch("endoscope.cli.EndoscopeAPIClient")
def test_pull_last_n(MockClient):
    client = _mock_client()
    five_sessions = [
        {**SAMPLE_SESSIONS[0], "session_id": f"sess-{i}"} for i in range(5)
    ]
    client.list_sessions.return_value = five_sessions
    MockClient.return_value = client

    result = runner.invoke(app, [*SHARED_OPTS, "pull", "--last", "2"])
    assert result.exit_code == 0
    assert client.pull_session.call_count == 2


@patch("endoscope.cli.EndoscopeAPIClient")
def test_pull_no_args_exits(MockClient):
    """pull without session_id and without --all/--last should exit 1."""
    client = _mock_client()
    MockClient.return_value = client

    result = runner.invoke(app, [*SHARED_OPTS, "pull"])
    assert result.exit_code == 1
    assert "Provide a session ID, or use --all / --last" in result.output


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@patch("endoscope.cli.EndoscopeAPIClient")
def test_delete_with_force(MockClient):
    client = _mock_client()
    MockClient.return_value = client

    result = runner.invoke(
        app, [*SHARED_OPTS, "delete", "abc-123", "--force"]
    )
    assert result.exit_code == 0
    client.delete_session.assert_called_once_with("abc-123")
    assert "Deleted session" in result.output


@patch("endoscope.cli.typer.confirm", return_value=True)
@patch("endoscope.cli.EndoscopeAPIClient")
def test_delete_requires_confirmation(MockClient, _mock_confirm):
    client = _mock_client()
    MockClient.return_value = client

    result = runner.invoke(app, [*SHARED_OPTS, "delete", "abc-123"])
    assert result.exit_code == 0
    client.delete_session.assert_called_once_with("abc-123")
    assert "Deleted session" in result.output


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------


@patch("endoscope.cli.EndoscopeAPIClient")
def test_prune_older_than(MockClient):
    client = _mock_client()
    MockClient.return_value = client

    result = runner.invoke(
        app, [*SHARED_OPTS, "prune", "--older-than", "7d", "--force"]
    )
    assert result.exit_code == 0
    client.prune_sessions.assert_called_once_with(
        older_than="7d", all=False
    )
    assert "Pruned" in result.output


@patch("endoscope.cli.EndoscopeAPIClient")
def test_prune_all(MockClient):
    client = _mock_client()
    MockClient.return_value = client

    result = runner.invoke(app, [*SHARED_OPTS, "prune", "--all", "--force"])
    assert result.exit_code == 0
    client.prune_sessions.assert_called_once_with(
        older_than=None, all=True
    )


@patch("endoscope.cli.EndoscopeAPIClient")
def test_prune_requires_flag(MockClient):
    """prune without --older-than or --all should exit 1."""
    client = _mock_client()
    MockClient.return_value = client

    result = runner.invoke(app, [*SHARED_OPTS, "prune"])
    assert result.exit_code == 1
    assert "Provide --older-than or --all" in result.output


# ---------------------------------------------------------------------------
# api-key
# ---------------------------------------------------------------------------


def test_api_key_generates_key():
    result = runner.invoke(app, ["api-key"])
    assert result.exit_code == 0
    key = result.output.strip()
    assert len(key) > 0
    import re

    assert re.match(r"^[A-Za-z0-9_\-+=]+$", key), f"Key not url-safe: {key}"


def test_api_key_length():
    result = runner.invoke(app, ["api-key"])
    assert result.exit_code == 0
    key = result.output.strip()
    assert len(key) >= 21


# ---------------------------------------------------------------------------
# missing required options
# ---------------------------------------------------------------------------


def test_missing_api_key_exits(monkeypatch):
    """When --api-key is empty and ENDO_API_KEY is unset, list should fail."""
    monkeypatch.delenv("ENDO_API_KEY", raising=False)
    monkeypatch.delenv("ENDO_PROJECT", raising=False)
    result = runner.invoke(app, ["--project", "test-proj", "list"])
    assert result.exit_code == 1
    assert "--api-key" in result.output
