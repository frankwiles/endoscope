"""Shared fixtures for integration tests.

Expects to run inside the Docker Compose network where RustFS is available
at ``http://rustfs:9000`` with the ``endoscope`` bucket already created
(by the ``init-bucket`` service in compose.yml).

Run via:  just test tests/test_app.py -v
"""

from __future__ import annotations

import os

import pytest
from starlette.testclient import TestClient

from endoscope.app import create_app
from endoscope.config import EndoscopeConfig


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
