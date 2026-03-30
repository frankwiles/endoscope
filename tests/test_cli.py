"""Integration tests for the Typer CLI commands.

All tests invoke the CLI via typer.testing.CliRunner and hit the real API
at http://api:8000 over the Docker Compose network.  No mocks are used.
"""

from __future__ import annotations

import json

import httpx
import pytest
from typer.testing import CliRunner

from endoscope.cli import app

runner = CliRunner()

API_URL = "http://api:8000"
API_KEY = "local-dev-api-key"
PROJECT = "local-test-project"

# Environment passed to every CLI invocation
CLI_ENV = {
    "ENDO_API_URL": API_URL,
    "ENDO_API_KEY": API_KEY,
    "ENDO_PROJECT": PROJECT,
}


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
# list
# ---------------------------------------------------------------------------


def test_list_shows_table():
    sid = _create_session()
    try:
        result = runner.invoke(app, ["list"], env=CLI_ENV)
        assert result.exit_code == 0
        assert sid in result.output
        assert "Sessions" in result.output
    finally:
        _delete_session(sid)


def test_list_json_flag():
    sid = _create_session()
    try:
        result = runner.invoke(app, ["list", "--json"], env=CLI_ENV)
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
        ids = [str(s["session_id"]) for s in parsed]
        assert sid in ids
    finally:
        _delete_session(sid)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def test_show_session_details(session_id):
    result = runner.invoke(app, ["show", session_id], env=CLI_ENV)
    assert result.exit_code == 0
    assert session_id in result.output
    assert PROJECT in result.output
    assert f"Session {session_id}" in result.output


def test_show_json_flag(session_id):
    result = runner.invoke(app, ["show", session_id, "--json"], env=CLI_ENV)
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert str(parsed["session_id"]) == session_id
    assert parsed["project"] == PROJECT


# ---------------------------------------------------------------------------
# pull
# ---------------------------------------------------------------------------


def test_pull_single_session(tmp_path, session_id):
    result = runner.invoke(
        app, ["pull", session_id, "--out-dir", str(tmp_path)], env=CLI_ENV
    )
    assert result.exit_code == 0
    assert "Pulled" in result.output
    assert session_id in result.output
    assert (tmp_path / session_id).is_dir()


def test_pull_all_sessions(tmp_path):
    sid1 = _create_session()
    sid2 = _create_session()
    try:
        result = runner.invoke(
            app, ["pull", "--all", "--out-dir", str(tmp_path)], env=CLI_ENV
        )
        assert result.exit_code == 0
        assert sid1 in result.output
        assert sid2 in result.output
        assert (tmp_path / sid1).is_dir()
        assert (tmp_path / sid2).is_dir()
    finally:
        _delete_session(sid1)
        _delete_session(sid2)


def test_pull_last_n(tmp_path):
    sids = [_create_session() for _ in range(3)]
    try:
        result = runner.invoke(
            app, ["pull", "--last", "2", "--out-dir", str(tmp_path)], env=CLI_ENV
        )
        assert result.exit_code == 0
        downloaded = [d for d in tmp_path.iterdir() if d.is_dir()]
        assert len(downloaded) == 2
    finally:
        for sid in sids:
            _delete_session(sid)


def test_pull_no_args_exits():
    result = runner.invoke(app, ["pull"], env=CLI_ENV)
    assert result.exit_code == 1
    assert "Provide a session ID, or use --all / --last" in result.output


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_with_force():
    sid = _create_session()
    result = runner.invoke(app, ["delete", sid, "--force"], env=CLI_ENV)
    assert result.exit_code == 0
    assert "Deleted session" in result.output


def test_delete_requires_confirmation():
    sid = _create_session()
    result = runner.invoke(app, ["delete", sid], env=CLI_ENV, input="y\n")
    assert result.exit_code == 0
    assert "Deleted session" in result.output


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------


def test_prune_older_than():
    result = runner.invoke(
        app, ["prune", "--older-than", "7d", "--force"], env=CLI_ENV
    )
    assert result.exit_code == 0
    assert "Pruned" in result.output


def test_prune_all():
    result = runner.invoke(app, ["prune", "--all", "--force"], env=CLI_ENV)
    assert result.exit_code == 0
    assert "Pruned" in result.output


def test_prune_requires_flag():
    result = runner.invoke(app, ["prune"], env=CLI_ENV)
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
