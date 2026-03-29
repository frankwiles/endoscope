"""Synchronous HTTP client for the Endoscope API.

Used by CLI commands to communicate with a running Endoscope server.
All methods are blocking (CLI is single-threaded) and raise on HTTP errors.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx


class EndoscopeAPIError(Exception):
    """Raised when the API returns a non-2xx response."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


class EndoscopeAPIClient:
    """Blocking client for the Endoscope REST API."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: str = "",
        project: str = "",
        timeout: float = 30.0,
        verify_ssl: bool = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._project = project
        self._timeout = timeout
        self._verify_ssl = verify_ssl

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> httpx.Response:
        """Send a request and raise on non-2xx."""
        resp = httpx.request(
            method,
            f"{self._base_url}{path}",
            headers=self._headers(),
            params=params,
            json=json_body,
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error", resp.text)
            except Exception:
                detail = resp.text
            raise EndoscopeAPIError(resp.status_code, detail)
        return resp

    # ------------------------------------------------------------------
    # Session operations
    # ------------------------------------------------------------------

    def list_sessions(self) -> list[dict]:
        """List all sessions for the server's configured project."""
        resp = self._request("GET", "/v1/sessions")
        return resp.json()

    def get_session(self, session_id: str) -> dict:
        """Get full details for a single session."""
        resp = self._request("GET", f"/v1/sessions/{session_id}")
        return resp.json()

    def delete_session(self, session_id: str) -> dict:
        """Delete a session and all its objects."""
        resp = self._request("DELETE", f"/v1/sessions/{session_id}")
        return resp.json()

    # ------------------------------------------------------------------
    # Prune
    # ------------------------------------------------------------------

    def prune_sessions(
        self, older_than: str | None = None, all: bool = False
    ) -> dict:
        """Bulk-delete sessions by age or all at once."""
        body: dict = {}
        if older_than:
            body["older_than"] = older_than
        if all:
            body["all"] = True
        resp = self._request("POST", "/v1/prune", json_body=body)
        return resp.json()

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def download_file(self, session_id: str, filename: str) -> bytes:
        """Download raw bytes for a file attached to a session."""
        resp = self._request(
            "GET",
            f"/v1/sessions/{session_id}/files/{filename}",
        )
        return resp.content

    def pull_session(self, session_id: str, out_dir: Path) -> Path:
        """Download a session's metadata and all files to a local directory.

        Creates ``<out_dir>/<session_id>/`` and writes:
        - ``metadata.json`` with full session data
        - Each file listed in the session's ``files`` field

        Returns the session directory path.
        """
        session = self.get_session(session_id)
        session_dir = out_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        # Write metadata
        meta_path = session_dir / "metadata.json"
        meta_path.write_text(json.dumps(session, indent=2))

        # Download each file
        for filename in session.get("files", []):
            data = self.download_file(session_id, filename)
            safe_name = Path(filename).name
            (session_dir / safe_name).write_bytes(data)

        return session_dir
