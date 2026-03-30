"""Integration tests for endoscope.app Starlette endpoints.

Runs against a real RustFS (MinIO-compatible) S3 backend inside Docker
Compose.  No mocks — every S3 operation hits actual storage.

Prerequisites:
    docker compose up -d rustfs init-bucket
    just test tests/test_app.py -v
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from endoscope.app import create_app
from endoscope.config import EndoscopeConfig


# ---------------------------------------------------------------------------
# Health checks — no S3 involvement
# ---------------------------------------------------------------------------


class TestHealthz:
    def test_returns_ok(self, client: TestClient):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.text == "ok"

    def test_bypasses_auth(self, cfg: EndoscopeConfig) -> None:
        """Health endpoints should be accessible without an API key."""
        cfg.api_key = "test-secret-key"
        protected_client = TestClient(create_app(cfg))
        resp = protected_client.get("/healthz")
        assert resp.status_code == 200
        assert resp.text == "ok"


class TestReadyz:
    def test_returns_ready(self, client: TestClient):
        resp = client.get("/readyz")
        assert resp.status_code == 200
        assert resp.text == "ready"

    def test_bypasses_auth(self, cfg: EndoscopeConfig) -> None:
        """Readiness endpoints should be accessible without an API key."""
        cfg.api_key = "test-secret-key"
        protected_client = TestClient(create_app(cfg))
        resp = protected_client.get("/readyz")
        assert resp.status_code == 200
        assert resp.text == "ready"


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    @pytest.fixture()
    def protected_client(self, cfg: EndoscopeConfig) -> TestClient:
        """Client with api_key set — all endpoints require the header."""
        cfg.api_key = "test-secret-key"
        return TestClient(create_app(cfg))

    def test_rejects_missing_key(self, protected_client: TestClient):
        resp = protected_client.get("/v1/sessions")
        assert resp.status_code == 401
        assert resp.json() == {"error": "unauthenticated"}

    def test_rejects_wrong_key(self, protected_client: TestClient):
        resp = protected_client.get(
            "/v1/sessions", headers={"x-api-key": "wrong"}
        )
        assert resp.status_code == 401
        assert resp.json() == {"error": "unauthenticated"}

    def test_allows_correct_key(self, protected_client: TestClient):
        resp = protected_client.get(
            "/v1/sessions", headers={"x-api-key": "test-secret-key"}
        )
        assert resp.status_code == 200

    def test_no_key_configured_allows_all(self, client: TestClient):
        """Default fixture has api_key="" — everything passes through."""
        resp = client.get("/v1/sessions")
        assert resp.status_code == 200

# ---------------------------------------------------------------------------
# POST /v1/sessions — create_session
# ---------------------------------------------------------------------------


class TestCreateSession:
    def test_creates_session_with_s3(self, client: TestClient):
        resp = client.post("/v1/sessions", json={"project": "my-proj"})
        assert resp.status_code == 201

        data = resp.json()
        assert data["project"] == "my-proj"
        assert "session_id" in data
        assert "timestamp" in data
        assert data["events"] == []
        assert data["files"] == []

        # The session_id is a valid UUID string.
        import uuid

        uuid.UUID(data["session_id"])

    def test_session_writes_metadata_to_s3(
        self, client: TestClient, cfg: EndoscopeConfig
    ):
        """Metadata JSON should be readable back from S3 via get_metadata."""
        resp = client.post("/v1/sessions", json={"project": "roundtrip-proj"})
        assert resp.status_code == 201
        session_id = resp.json()["session_id"]

        # Verify we can read the metadata back via the get_metadata endpoint.
        meta = client.get(f"/v1/sessions/{session_id}/metadata")
        assert meta.status_code == 200
        assert meta.json()["session_id"] == session_id
        assert meta.json()["project"] == "roundtrip-proj"

    def test_invalid_json_body_raises(self, client: TestClient):
        """POST with non-JSON body — request.json() raises JSONDecodeError."""
        with pytest.raises(Exception):
            client.post(
                "/v1/sessions",
                content=b"not json",
                headers={"Content-Type": "text/plain"},
            )


# ---------------------------------------------------------------------------
# POST /v1/sessions/{id}/events — add_event
# ---------------------------------------------------------------------------


class TestAddEvent:
    def test_add_event_to_existing_session(self, client: TestClient):
        # Create a session first.
        create_resp = client.post("/v1/sessions", json={"project": "evt-proj"})
        session_id = create_resp.json()["session_id"]

        # Add an event.
        event = {"type": "error", "message": "something broke", "code": 500}
        resp = client.post(
            f"/v1/sessions/{session_id}/events",
            json=event,
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

        # Verify the event is persisted in metadata.
        meta = client.get(f"/v1/sessions/{session_id}/metadata")
        assert meta.status_code == 200
        events = meta.json()["events"]
        assert len(events) == 1
        assert events[0] == event

    def test_add_multiple_events(self, client: TestClient):
        create_resp = client.post("/v1/sessions", json={"project": "multi-evt"})
        session_id = create_resp.json()["session_id"]

        for i in range(3):
            resp = client.post(
                f"/v1/sessions/{session_id}/events",
                json={"index": i},
            )
            assert resp.status_code == 200

        meta = client.get(f"/v1/sessions/{session_id}/metadata")
        assert len(meta.json()["events"]) == 3

    def test_add_event_to_nonexistent_session(self, client: TestClient):
        resp = client.post(
            "/v1/sessions/00000000-0000-0000-0000-000000000000/events",
            json={"type": "test"},
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "session not found"}

    def test_add_event_no_bucket_configured(self):
        cfg = EndoscopeConfig(
            api_key="",
            project="test",
            s3_access_key="x",
            s3_secret_key="x",
            s3_bucket="",  # no bucket
        )
        client = TestClient(create_app(cfg))
        resp = client.post(
            "/v1/sessions/00000000-0000-0000-0000-000000000000/events",
            json={"type": "test"},
        )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /v1/sessions/{id}/files — add_file
# ---------------------------------------------------------------------------


class TestAddFile:
    def test_add_file_by_filename(self, client: TestClient):
        create_resp = client.post("/v1/sessions", json={"project": "file-proj"})
        session_id = create_resp.json()["session_id"]

        resp = client.post(
            f"/v1/sessions/{session_id}/files",
            data={"filename": "screenshot.png"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "upload_url" in data
        # The presigned URL should reference the correct key.
        assert "screenshot.png" in data["upload_url"]

        # Verify the file is recorded in metadata.
        meta = client.get(f"/v1/sessions/{session_id}/metadata")
        files = meta.json()["files"]
        assert "screenshot.png" in files

    def test_add_file_uses_uploaded_filename_as_fallback(self, client: TestClient):
        """When no explicit filename, use the uploaded file's name."""
        create_resp = client.post("/v1/sessions", json={"project": "fb-proj"})
        session_id = create_resp.json()["session_id"]

        resp = client.post(
            f"/v1/sessions/{session_id}/files",
            files={"file": ("dump.bin", b"\x00\x01\x02", "application/octet-stream")},
        )
        assert resp.status_code == 200
        assert "upload_url" in resp.json()
        assert "dump.bin" in resp.json()["upload_url"]

    def test_add_file_to_nonexistent_session(self, client: TestClient):
        resp = client.post(
            "/v1/sessions/00000000-0000-0000-0000-000000000000/files",
            data={"filename": "x.txt"},
        )
        assert resp.status_code == 404

    def test_add_file_no_bucket_configured(self):
        cfg = EndoscopeConfig(
            api_key="",
            project="test",
            s3_access_key="x",
            s3_secret_key="x",
            s3_bucket="",
        )
        client = TestClient(create_app(cfg))
        resp = client.post(
            "/v1/sessions/00000000-0000-0000-0000-000000000000/files",
            data={"filename": "x.txt"},
        )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /v1/sessions/{id}/metadata — get_metadata
# ---------------------------------------------------------------------------


class TestGetMetadata:
    def test_get_metadata_for_existing_session(self, client: TestClient):
        create_resp = client.post(
            "/v1/sessions", json={"project": "meta-proj"}
        )
        session_id = create_resp.json()["session_id"]

        # Add a couple events and a file to exercise the full shape.
        client.post(
            f"/v1/sessions/{session_id}/events",
            json={"action": "click"},
        )
        client.post(
            f"/v1/sessions/{session_id}/files",
            data={"filename": "log.txt"},
        )

        resp = client.get(f"/v1/sessions/{session_id}/metadata")
        assert resp.status_code == 200

        data = resp.json()
        assert data["session_id"] == session_id
        assert data["project"] == "meta-proj"
        assert "timestamp" in data
        assert len(data["events"]) == 1
        assert data["events"][0]["action"] == "click"
        assert data["files"] == ["log.txt"]

    def test_get_metadata_nonexistent_session(self, client: TestClient):
        resp = client.get(
            "/v1/sessions/00000000-0000-0000-0000-000000000000/metadata"
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "session not found"}

    def test_get_metadata_no_bucket_configured(self):
        cfg = EndoscopeConfig(
            api_key="",
            project="test",
            s3_access_key="x",
            s3_secret_key="x",
            s3_bucket="",
        )
        client = TestClient(create_app(cfg))
        resp = client.get("/v1/sessions/00000000-0000-0000-0000-000000000000/metadata")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Full workflow — end-to-end exercise of all endpoints
# ---------------------------------------------------------------------------


class TestFullWorkflow:
    def test_create_event_file_metadata_roundtrip(self, client: TestClient):
        """Exercise the complete session lifecycle: create → event → file → read."""
        # 1. Create session.
        create = client.post("/v1/sessions", json={"project": "e2e-proj"})
        assert create.status_code == 201
        sid = create.json()["session_id"]

        # 2. Add events.
        for label in ("start", "error", "end"):
            r = client.post(
                f"/v1/sessions/{sid}/events", json={"label": label}
            )
            assert r.status_code == 200

        # 3. Request upload URLs for files.
        for fname in ("core.dump", "screenshot.png"):
            r = client.post(
                f"/v1/sessions/{sid}/files", data={"filename": fname}
            )
            assert r.status_code == 200
            assert "upload_url" in r.json()

        # 4. Retrieve final metadata.
        meta = client.get(f"/v1/sessions/{sid}/metadata")
        assert meta.status_code == 200

        body = meta.json()
        assert body["session_id"] == sid
        assert body["project"] == "e2e-proj"
        assert len(body["events"]) == 3
        assert [e["label"] for e in body["events"]] == ["start", "error", "end"]
        assert set(body["files"]) == {"core.dump", "screenshot.png"}



# ---------------------------------------------------------------------------
# GET /v1/sessions — list_sessions
# ---------------------------------------------------------------------------


class TestListSessions:
    def test_list_sessions_empty(self, client: TestClient):
        """GET /v1/sessions returns empty list when no sessions exist for the project."""
        resp = client.get("/v1/sessions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_sessions_after_create(self, client: TestClient, cfg: EndoscopeConfig):
        """After creating a session, it appears in the list with correct fields."""
        create = client.post("/v1/sessions", json={"project": cfg.project})
        assert create.status_code == 201
        sid = create.json()["session_id"]

        resp = client.get("/v1/sessions")
        assert resp.status_code == 200

        sessions = resp.json()
        assert isinstance(sessions, list)
        assert len(sessions) >= 1

        # Find the session we just created
        matched = [s for s in sessions if s["session_id"] == sid]
        assert len(matched) == 1

        summary = matched[0]
        assert "session_id" in summary
        assert "timestamp" in summary
        assert summary["project"] == cfg.project
        assert "event_count" in summary
        assert "file_count" in summary


# ---------------------------------------------------------------------------
# GET /v1/sessions/{id} — get_session
# ---------------------------------------------------------------------------


class TestGetSession:
    def test_get_session_by_id(self, client: TestClient):
        """Create a session with events and files, then GET it by ID."""
        create = client.post("/v1/sessions", json={"project": "test-project"})
        assert create.status_code == 201
        sid = create.json()["session_id"]

        # Add an event and a file to exercise full shape
        client.post(f"/v1/sessions/{sid}/events", json={"type": "test"})
        client.post(f"/v1/sessions/{sid}/files", data={"filename": "log.txt"})

        resp = client.get(f"/v1/sessions/{sid}")
        assert resp.status_code == 200

        data = resp.json()
        assert data["session_id"] == sid
        assert data["project"] == "test-project"
        assert "timestamp" in data
        assert isinstance(data["events"], list)
        assert len(data["events"]) == 1
        assert data["events"][0]["type"] == "test"
        assert data["files"] == ["log.txt"]

    def test_get_session_not_found(self, client: TestClient):
        """GET a valid UUID that doesn't exist returns 404."""
        resp = client.get("/v1/sessions/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404
        assert resp.json() == {"error": "session not found"}

    def test_get_session_invalid_uuid(self, client: TestClient):
        """GET with an invalid UUID format returns 400."""
        resp = client.get("/v1/sessions/not-a-uuid")
        assert resp.status_code == 400
        assert resp.json() == {"error": "invalid session id"}


# ---------------------------------------------------------------------------
# DELETE /v1/sessions/{id} — delete_session
# ---------------------------------------------------------------------------


class TestDeleteSession:
    def test_delete_session(self, client: TestClient):
        """Create a session, delete it, then confirm it's gone."""
        create = client.post("/v1/sessions", json={"project": "test-project"})
        assert create.status_code == 201
        sid = create.json()["session_id"]

        # Delete it
        resp = client.delete(f"/v1/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": sid}

        # Confirm it's gone
        get_resp = client.get(f"/v1/sessions/{sid}")
        assert get_resp.status_code == 404

    def test_delete_session_not_found(self, client: TestClient):
        """DELETE a valid UUID that doesn't exist returns 404."""
        resp = client.delete("/v1/sessions/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404
        assert resp.json() == {"error": "session not found"}

    def test_delete_session_invalid_uuid(self, client: TestClient):
        """DELETE with an invalid UUID format returns 400."""
        resp = client.delete("/v1/sessions/not-a-uuid")
        assert resp.status_code == 400
        assert resp.json() == {"error": "invalid session id"}


# ---------------------------------------------------------------------------
# POST /v1/prune — prune_sessions
# ---------------------------------------------------------------------------


class TestPrune:
    def test_prune_by_age(self, client: TestClient):
        """Prune with older_than returns correct shape {pruned: N}."""
        resp = client.post("/v1/prune", json={"older_than": "999d"})
        assert resp.status_code == 200
        data = resp.json()
        assert "pruned" in data
        assert isinstance(data["pruned"], int)
        assert data["pruned"] >= 0

    def test_prune_all(self, client: TestClient, cfg: EndoscopeConfig):
        """Create a session, prune all, then verify list is empty."""
        create = client.post("/v1/sessions", json={"project": cfg.project})
        assert create.status_code == 201

        resp = client.post("/v1/prune", json={"all": True})
        assert resp.status_code == 200
        data = resp.json()
        assert "pruned" in data
        assert isinstance(data["pruned"], int)
        assert data["pruned"] >= 1

        # Verify list is now empty
        list_resp = client.get("/v1/sessions")
        assert list_resp.status_code == 200
        assert list_resp.json() == []

    def test_prune_invalid_duration(self, client: TestClient):
        """Prune with unparseable older_than returns 400."""
        resp = client.post("/v1/prune", json={"older_than": "invalid"})
        assert resp.status_code == 400

    def test_prune_no_criteria(self, client: TestClient):
        """Prune with empty body returns 400."""
        resp = client.post("/v1/prune", json={})
        assert resp.status_code == 400
        assert "error" in resp.json()


# ---------------------------------------------------------------------------
# GET /v1/sessions/{id}/files/{filename} — download_file
# ---------------------------------------------------------------------------


class TestDownloadFile:
    def test_download_file_not_found(self, client: TestClient):
        """Download a file from a non-existent session returns 404."""
        resp = client.get(
            "/v1/sessions/00000000-0000-0000-0000-000000000000/files/missing.txt"
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "file not found"}
