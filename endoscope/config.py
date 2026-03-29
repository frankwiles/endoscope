"""Typed configuration for Endoscope.

All environment-derived settings live here. The object is constructed once
(in the CLI or a test fixture) and passed into ``create_app``; nothing else
reads environment variables directly.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EndoscopeConfig:
    api_key: str  # ENDO_API_KEY
    project: str  # ENDO_PROJECT
    s3_access_key: str  # ENDO_S3_ACCESS_KEY
    s3_secret_key: str  # ENDO_S3_SECRET_KEY
    s3_bucket: str  # ENDO_S3_BUCKET
    s3_endpoint: str = "https://s3.us-east-1.amazonaws.com"  # ENDO_S3_ENDPOINT
    s3_region: str = "us-east-1"  # ENDO_S3_REGION
    host: str = "0.0.0.0"  # ENDO_HOST
    port: int = 8000  # ENDO_PORT
    debug: bool = False  # ENDO_DEBUG / ENDO_RELOAD
    pretty_json_logs: bool = False  # ENDO_PRETTY_JSON_LOGS
