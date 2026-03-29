"""Python SDK for the Endoscope debug artifact capture system.

Usage::

    from endoscope import EndoscopeClient

    client = EndoscopeClient.from_env()
    session = client.start_session(project="my-app")

    session.event("step", {"x": 1})
    session.file("output.txt", b"hello")
"""

from __future__ import annotations

import structlog
import os
from typing import Any

import httpx

log = structlog.get_logger()


class EndoscopeAuthError(Exception):
    """Authentication failure when ``raise_on_auth_error`` is enabled."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(
            f"Authentication failed (HTTP {status_code}): {detail}"
        )


class EndoscopeError(Exception):
    """Non-auth API error when ``raise_on_auth_error`` is enabled."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"API error (HTTP {status_code}): {detail}")


def _extract_error(resp: httpx.Response) -> str:
    """Pull a human-readable error message from an API response."""
    try:
        return resp.json().get("error", resp.text)
    except Exception:
        return resp.text


class Session:
    """An active debug session for recording events and files.

    Obtain via :meth:`EndoscopeClient.start_session`.  When
    ``raise_on_auth_error=False`` (default), the session degrades
    gracefully — calls become no-ops after the first failure.
    """

    def __init__(
        self,
        session_id: str,
        project: str,
        _http: httpx.Client,
        _raise_on_error: bool = False,
        _disabled: bool = False,
    ) -> None:
        self.session_id = session_id
        self.project = project
        self._http = _http
        self._raise_on_error = _raise_on_error
        self._disabled = _disabled

    def __repr__(self) -> str:
        tag = "disabled" if self._disabled else "active"
        return f"Session(id={self.session_id!r}, project={self.project!r}, {tag})"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def event(self, name: str, data: dict[str, Any] | None = None) -> None:
        """Record a structured event in this session.

        Args:
            name: Event type (e.g. ``"step"``, ``"error"``).
            data: Optional payload merged into the event.
        """
        if self._disabled:
            return
        try:
            payload = {"type": name, **(data or {})}
            resp = self._http.post(
                f"/v1/sessions/{self.session_id}/events",
                json=payload,
            )
            self._check(resp)
        except (EndoscopeAuthError, EndoscopeError):
            raise
        except Exception:
            if self._raise_on_error:
                raise
            self._disabled = True
            log.warning("endoscope.event.failed", exc_info=True)

    def file(self, filename: str, data: bytes | str) -> None:
        """Attach a file to this session.

        Requests a presigned upload URL from the API, then uploads the
        content directly to S3.

        Args:
            filename: Name for the file in the session.
            data: File content (bytes or UTF-8 string).
        """
        if self._disabled:
            return
        if isinstance(data, str):
            data = data.encode()

        # 1. Register file with the API and obtain a presigned S3 URL.
        try:
            resp = self._http.post(
                f"/v1/sessions/{self.session_id}/files",
                data={"filename": filename},
            )
        except httpx.RequestError:
            if self._raise_on_error:
                raise
            self._disabled = True
            log.warning("endoscope.file.register_failed", exc_info=True)
            return

        self._check(resp)
        if self._disabled:
            return

        # 2. Upload content directly to S3 via the presigned URL.
        upload_url = resp.json()["upload_url"]
        try:
            put_resp = httpx.put(upload_url, content=data, timeout=30.0)
            if put_resp.status_code >= 400:
                raise EndoscopeError(
                    put_resp.status_code,
                    f"S3 upload failed for {filename!r}",
                )
        except (EndoscopeError, httpx.RequestError) as exc:
            if self._raise_on_error:
                raise
            # Log but don't disable — only the S3 leg failed;
            # the session itself is still healthy.
            log.warning("endoscope.file.upload_failed: %s", exc)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check(self, resp: httpx.Response) -> None:
        """Inspect an API response; disable or raise on errors."""
        if resp.status_code == 401:
            self._disabled = True
            detail = _extract_error(resp)
            if self._raise_on_error:
                raise EndoscopeAuthError(resp.status_code, detail)
            log.warning("endoscope.auth_failed: %s", detail)
            return

        if resp.status_code >= 400:
            self._disabled = True
            detail = _extract_error(resp)
            if self._raise_on_error:
                raise EndoscopeError(resp.status_code, detail)
            log.warning("endoscope.api_error: %s", detail)


class EndoscopeClient:
    """Python SDK for the Endoscope debug artifact capture system.

    Args:
        api_url: Base URL of the Endoscope API.
        api_key: API key for authentication.
        project: Default project name (overridable per session).
        raise_on_auth_error: When ``True``, raise on any API error
            instead of silently degrading.  Default ``False``.
        timeout: HTTP request timeout in seconds.
        verify_ssl: Verify TLS certificates.
    """

    def __init__(
        self,
        api_url: str = "http://localhost:8000",
        api_key: str = "",
        project: str = "",
        raise_on_auth_error: bool = False,
        timeout: float = 30.0,
        verify_ssl: bool = True,
        *,
        _http: httpx.Client | None = None,
    ) -> None:
        self._project = project
        self._raise_on_error = raise_on_auth_error

        if _http is not None:
            self._http = _http
        else:
            headers: dict[str, str] = {}
            if api_key:
                headers["x-api-key"] = api_key
            self._http = httpx.Client(
                base_url=api_url.rstrip("/"),
                headers=headers,
                timeout=timeout,
                verify=verify_ssl,
            )

    def __enter__(self) -> EndoscopeClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._http.close()

    @classmethod
    def from_env(
        cls,
        *,
        raise_on_auth_error: bool = False,
        timeout: float = 30.0,
        verify_ssl: bool = True,
    ) -> EndoscopeClient:
        """Create a client from environment variables.

        Reads:

        - ``ENDO_API_URL``  — API base URL (default ``http://localhost:8000``)
        - ``ENDO_API_KEY``  — API key
        - ``ENDO_PROJECT``  — Default project name
        - ``ENDO_INSECURE`` — ``true`` / ``1`` / ``yes`` to skip TLS verification
        """
        api_url = os.environ.get("ENDO_API_URL", "http://localhost:8000")
        api_key = os.environ.get("ENDO_API_KEY", "")
        project = os.environ.get("ENDO_PROJECT", "")
        insecure = os.environ.get("ENDO_INSECURE", "").lower() in (
            "1",
            "true",
            "yes",
        )

        return cls(
            api_url=api_url,
            api_key=api_key,
            project=project,
            raise_on_auth_error=raise_on_auth_error,
            timeout=timeout,
            verify_ssl=not insecure,
        )

    def start_session(
        self,
        project: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Session:
        """Start a new debug session.

        Args:
            project: Project name.  Falls back to the client default.
            metadata: Optional metadata to attach to the session.

        Returns:
            A :class:`Session` for recording events and files.
        """
        proj = project or self._project

        try:
            resp = self._http.post(
                "/v1/sessions",
                json={"project": proj, "metadata": metadata},
            )
        except Exception:
            if self._raise_on_error:
                raise
            log.warning("endoscope.start_session.failed", exc_info=True)
            return self._disabled_session(proj)

        if resp.status_code >= 400:
            detail = _extract_error(resp)
            if self._raise_on_error:
                exc_class = (
                    EndoscopeAuthError
                    if resp.status_code == 401
                    else EndoscopeError
                )
                raise exc_class(resp.status_code, detail)
            log.warning("endoscope.start_session.error: %s", detail)
            return self._disabled_session(proj)

        data = resp.json()
        return Session(
            session_id=data["session_id"],
            project=data["project"],
            _http=self._http,
            _raise_on_error=self._raise_on_error,
        )

    def _disabled_session(self, project: str) -> Session:
        """Return a no-op session used after auth/network failures."""
        return Session(
            session_id="",
            project=project,
            _http=self._http,
            _raise_on_error=False,
            _disabled=True,
        )
