"""Shared fixtures for integration tests.

Expects to run inside the Docker Compose network where RustFS is available
at ``http://rustfs:9000`` with the ``endoscope`` bucket already created
(by the ``init-bucket`` service in compose.yml).

Run via:  just test tests/test_app.py -v
"""

from __future__ import annotations

import os
import uuid

import pytest
from starlette.testclient import TestClient

from endoscope.app import create_app
from endoscope.config import EndoscopeConfig
from endoscope.services import SessionService
from endoscope.storage import S3Storage


def _cfg_from_env() -> EndoscopeConfig:
    """Build config from Docker Compose environment variables."""
    return EndoscopeConfig(
        api_key=os.getenv("ENDO_API_KEY", ""),
        project=os.getenv("ENDO_PROJECT", "test-project"),
        s3_endpoint=os.getenv("ENDO_S3_ENDPOINT", "http://rustfs:9000"),
        s3_access_key=os.getenv("ENDO_S3_ACCESS_KEY", "rustfsadmin"),
        s3_secret_key=os.getenv("ENDO_S3_SECRET_KEY", "rustfsadmin"),
        s3_bucket=os.getenv("ENDO_S3_BUCKET", "endoscope"),
        s3_region=os.getenv("ENDO_S3_REGION", "us-east-1"),
    )


@pytest.fixture()
def cfg() -> EndoscopeConfig:
    cfg = _cfg_from_env()
    cfg.api_key = ""  # default fixture: no auth
    return cfg


@pytest.fixture()
def client(cfg: EndoscopeConfig) -> TestClient:
    """Starlette test client wired to a real RustFS-backed config."""
    return TestClient(create_app(cfg))


@pytest.fixture()
def storage() -> S3Storage:
    """Real S3Storage pointed at the Docker Compose RustFS instance."""
    return S3Storage(
        endpoint_url=os.getenv("ENDO_S3_ENDPOINT", "http://rustfs:9000"),
        access_key=os.getenv("ENDO_S3_ACCESS_KEY", "rustfsadmin"),
        secret_key=os.getenv("ENDO_S3_SECRET_KEY", "rustfsadmin"),
        bucket=os.getenv("ENDO_S3_BUCKET", "endoscope"),
    )


@pytest.fixture()
def project() -> str:
    """Unique project name per test to avoid data collisions."""
    return f"test-{uuid.uuid4()}"


@pytest.fixture()
def svc(storage: S3Storage) -> SessionService:
    """SessionService wired to real S3Storage."""
    return SessionService(storage=storage)
