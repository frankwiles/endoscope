"""Integration tests for the Endoscope Python SDK.

Uses Starlette's TestClient (sync httpx.Client with ASGI transport) to
run the SDK against the real Starlette application — no mocks, no
network.  S3 operations hit RustFS via Docker Compose
(``just test tests/test_sdk.py -v``).
"""

from __future__ import annotations

import os
from uuid import UUID

import pytest
from starlette.testclient import TestClient

from endoscope.app import create_app
from endoscope.config import EndoscopeConfig
from endoscope.sdk import (
    EndoscopeAuthError,
    EndoscopeClient,
    EndoscopeError,
    Session,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cfg(**overrides: object) -> EndoscopeConfig:
    defaults: dict = dict(
        api_key="",
        project="sdk-test",
        s3_endpoint=os.getenv("ENDO_S3_ENDPOINT", "http://rustfs:9000"),
        s3_access_key=os.getenv("ENDO_S3_ACCESS_KEY", "rustfsadmin"),
        s3_secret_key=os.getenv("ENDO_S3_SECRET_KEY", "rustfsadmin"),
        s3_bucket=os.getenv("ENDO_S3_BUCKET", "endoscope"),
        s3_region=os.getenv("ENDO_S3_REGION", "us-east-1"),
    )
    defaults.update(overrides)
    return EndoscopeConfig(**defaults)


def _make_sdk(
    cfg: EndoscopeConfig | None = None,
    api_key: str = "",
    raise_on_error: bool = False,
) -> tuple[EndoscopeClient, TestClient]:
    """Return (SDK client, Starlette TestClient) sharing the same app.

    The TestClient can be used to verify state independently of the SDK.
    """
    cfg = cfg or _make_cfg()
    app = create_app(cfg)
    tc = TestClient(app)
    headers: dict[str, str] = {}
    if api_key:
        headers["x-api-key"] = api_key
    tc.headers.update(headers)

    sdk = EndoscopeClient(
        project=cfg.project,
        raise_on_auth_error=raise_on_error,
        _http=tc,
    )
    return sdk, tc


# ---------------------------------------------------------------------------
# start_session
# ---------------------------------------------------------------------------


class TestStartSession:
    def test_returns_session_with_id(self):
        sdk, tc = _make_sdk()
        session = sdk.start_session()
        assert session.session_id
        UUID(session.session_id)  # valid UUID
        assert isinstance(session, Session)

    def test_uses_default_project(self):
        sdk, _ = _make_sdk()
        session = sdk.start_session()
        assert session.project == "sdk-test"

    def test_project_override(self):
        sdk, _ = _make_sdk()
        session = sdk.start_session(project="other")
        assert session.project == "other"

    def test_session_with_metadata(self):
        sdk, tc = _make_sdk()
        session = sdk.start_session(metadata={"env": "staging"})
        assert session.session_id

        resp = tc.get(f"/v1/sessions/{session.session_id}")
        assert resp.json()["metadata"] == {"env": "staging"}

    def test_repr_active(self):
        sdk, _ = _make_sdk()
        session = sdk.start_session()
        r = repr(session)
        assert "active" in r
        assert session.session_id in r


# ---------------------------------------------------------------------------
# event
# ---------------------------------------------------------------------------


class TestEvent:
    def test_record_event(self):
        sdk, tc = _make_sdk()
        session = sdk.start_session()
        session.event("step", {"x": 1})

        resp = tc.get(f"/v1/sessions/{session.session_id}")
        events = resp.json()["events"]
        assert len(events) == 1
        assert events[0] == {"type": "step", "x": 1}

    def test_event_without_data(self):
        sdk, tc = _make_sdk()
        session = sdk.start_session()
        session.event("ping")

        resp = tc.get(f"/v1/sessions/{session.session_id}")
        events = resp.json()["events"]
        assert events[0] == {"type": "ping"}

    def test_multiple_events(self):
        sdk, tc = _make_sdk()
        session = sdk.start_session()
        session.event("a", {"i": 1})
        session.event("b", {"i": 2})
        session.event("c", {"i": 3})

        resp = tc.get(f"/v1/sessions/{session.session_id}")
        events = resp.json()["events"]
        assert len(events) == 3
        assert [e["type"] for e in events] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# file
# ---------------------------------------------------------------------------


class TestFile:
    def test_file_registers_and_uploads(self):
        sdk, tc = _make_sdk()
        session = sdk.start_session()
        session.file("hello.txt", b"hello world")

        # File appears in session metadata
        resp = tc.get(f"/v1/sessions/{session.session_id}")
        assert "hello.txt" in resp.json()["files"]

        # File content is downloadable
        dl = tc.get(f"/v1/sessions/{session.session_id}/files/hello.txt")
        assert dl.status_code == 200
        assert dl.content == b"hello world"

    def test_file_accepts_string(self):
        sdk, tc = _make_sdk()
        session = sdk.start_session()
        session.file("note.txt", "some text")

        dl = tc.get(f"/v1/sessions/{session.session_id}/files/note.txt")
        assert dl.status_code == 200
        assert dl.content == b"some text"

    def test_multiple_files(self):
        sdk, tc = _make_sdk()
        session = sdk.start_session()
        session.file("a.txt", b"aaa")
        session.file("b.txt", b"bbb")

        resp = tc.get(f"/v1/sessions/{session.session_id}")
        assert set(resp.json()["files"]) == {"a.txt", "b.txt"}


# ---------------------------------------------------------------------------
# Auth error handling
# ---------------------------------------------------------------------------


class TestAuthError:
    def test_no_raise_on_auth_failure(self):
        cfg = _make_cfg(api_key="server-secret")
        sdk, tc = _make_sdk(cfg=cfg, api_key="wrong-key")
        session = sdk.start_session()

        assert session._disabled
        assert session.session_id == ""

        # Subsequent calls are silent no-ops
        session.event("step", {"x": 1})
        session.file("f.txt", b"data")

    def test_raise_on_auth_failure(self):
        cfg = _make_cfg(api_key="server-secret")
        sdk, tc = _make_sdk(cfg=cfg, api_key="wrong-key", raise_on_error=True)

        with pytest.raises(EndoscopeAuthError) as exc_info:
            sdk.start_session()
        assert exc_info.value.status_code == 401

    def test_event_raises_on_auth_failure(self):
        cfg = _make_cfg(api_key="secret")
        sdk, tc = _make_sdk(cfg=cfg, api_key="secret", raise_on_error=True)
        session = sdk.start_session()
        assert not session._disabled

        # Break auth to simulate a mid-session key rotation
        tc.headers["x-api-key"] = "wrong"

        with pytest.raises(EndoscopeAuthError):
            session.event("test")

    def test_non_auth_error_raises_in_strict_mode(self):
        sdk, tc = _make_sdk(raise_on_error=True)
        sdk.start_session()

        fake = Session(
            session_id="00000000-0000-0000-0000-000000000000",
            project="sdk-test",
            _http=tc,
            _raise_on_error=True,
        )
        with pytest.raises(EndoscopeError):
            fake.event("test")


# ---------------------------------------------------------------------------
# Disabled session
# ---------------------------------------------------------------------------


class TestDisabledSession:
    def test_disabled_session_events_are_noop(self):
        sdk, tc = _make_sdk()
        session = sdk.start_session()
        event_count_before = len(
            tc.get(f"/v1/sessions/{session.session_id}").json()["events"]
        )

        session._disabled = True
        session.event("should-not-send", {})

        events = tc.get(f"/v1/sessions/{session.session_id}").json()["events"]
        assert len(events) == event_count_before

    def test_disabled_session_files_are_noop(self):
        sdk, tc = _make_sdk()
        session = sdk.start_session()
        file_count_before = len(
            tc.get(f"/v1/sessions/{session.session_id}").json()["files"]
        )

        session._disabled = True
        session.file("noop.txt", b"data")

        files = tc.get(f"/v1/sessions/{session.session_id}").json()["files"]
        assert len(files) == file_count_before

    def test_repr_disabled(self):
        sdk, _ = _make_sdk()
        session = sdk.start_session()
        session._disabled = True
        assert "disabled" in repr(session)


# ---------------------------------------------------------------------------
# from_env
# ---------------------------------------------------------------------------


class TestFromEnv:
    def test_reads_project(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ENDO_PROJECT", "env-proj")
        client = EndoscopeClient.from_env()
        assert client._project == "env-proj"

    def test_reads_api_key(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ENDO_API_KEY", "test-key")
        client = EndoscopeClient.from_env()
        assert client._http.headers["x-api-key"] == "test-key"

    def test_no_api_key_no_header(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ENDO_API_KEY", raising=False)
        client = EndoscopeClient.from_env()
        assert "x-api-key" not in client._http.headers

    def test_defaults_when_env_empty(self, monkeypatch: pytest.MonkeyPatch):
        for var in ("ENDO_API_URL", "ENDO_API_KEY", "ENDO_PROJECT", "ENDO_INSECURE"):
            monkeypatch.delenv(var, raising=False)
        client = EndoscopeClient.from_env()
        assert client._project == ""
        assert "x-api-key" not in client._http.headers


# ---------------------------------------------------------------------------
# Full workflow
# ---------------------------------------------------------------------------


class TestFullWorkflow:
    def test_create_event_file_roundtrip(self):
        sdk, tc = _make_sdk()
        session = sdk.start_session()

        session.event("start")
        session.event("error", {"msg": "oops"})
        session.file("log.txt", b"line1\nline2")
        session.event("end")

        resp = tc.get(f"/v1/sessions/{session.session_id}")
        data = resp.json()

        assert len(data["events"]) == 3
        assert data["events"][0] == {"type": "start"}
        assert data["events"][1] == {"type": "error", "msg": "oops"}
        assert data["events"][2] == {"type": "end"}
        assert data["files"] == ["log.txt"]

        dl = tc.get(f"/v1/sessions/{session.session_id}/files/log.txt")
        assert dl.content == b"line1\nline2"
