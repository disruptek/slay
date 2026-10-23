"""
Pytest configuration and shared fixtures for slay tests.
"""

import pytest
import os
from unittest.mock import patch


@pytest.fixture
def local_backend():
    """Force use of local backend for tests"""
    import slay

    slay.set_backend("local")
    yield
    # No cleanup needed


@pytest.fixture
def clear_cache():
    """Clear slay cache before and after test"""
    import slay

    slay.wrappers.clear_cache()
    yield
    slay.wrappers.clear_cache()


@pytest.fixture
def mock_s3_bucket(tmp_path):
    """Mock S3 storage using local filesystem"""
    os.environ["SLAY_STORAGE_BUCKET"] = str(tmp_path)
    yield tmp_path
    if "SLAY_STORAGE_BUCKET" in os.environ:
        del os.environ["SLAY_STORAGE_BUCKET"]
