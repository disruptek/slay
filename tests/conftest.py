"""
Pytest configuration and shared fixtures for slay tests.
"""

import os
import time

import pytest


@pytest.fixture
def local_backend():
    """Force use of local backend for tests"""
    import slay

    slay.set_backend("local")
    yield
    # No cleanup needed


@pytest.fixture
def sleeps(monkeypatch):
    """The seconds passed to time.sleep, which returns at once"""
    recorded = []
    monkeypatch.setattr("time.sleep", recorded.append)
    return recorded


@pytest.fixture
def time_zone():
    """
    A function that sets the local time zone of the process, as a POSIX
    TZ string (for example "EST5", which is UTC-5), until the test ends
    """
    saved = os.environ.get("TZ")

    def set_zone(zone):
        os.environ["TZ"] = zone
        time.tzset()

    yield set_zone
    if saved is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = saved
    time.tzset()


@pytest.fixture
def mock_s3_bucket(tmp_path, monkeypatch):
    """Mock S3 storage using local filesystem"""
    monkeypatch.setenv("SLAY_STORAGE_BUCKET", str(tmp_path))
    return tmp_path


@pytest.fixture
def s3_bucket(monkeypatch):
    """An S3 bucket in moto, named by SLAY_STORAGE_BUCKET; no AWS calls"""
    moto = pytest.importorskip("moto")
    import boto3

    for name in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

    with moto.mock_aws():
        boto3.client("s3").create_bucket(Bucket="slay-test-bucket")
        monkeypatch.setenv("SLAY_STORAGE_BUCKET", "slay-test-bucket")
        yield "slay-test-bucket"
