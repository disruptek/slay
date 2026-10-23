"""
Unit tests for storage tier selection and the storage bucket setting.
"""

import pytest
import slay
from slay.exceptions import StorageError

LARGE = "x" * 2000  # serializes to more than the 1 KB inline limit


class TestStorageBucketSetting:
    """SLAY_STORAGE_BUCKET is required for values of 1 KB or more"""

    def test_small_value_needs_no_bucket(self, local_backend, monkeypatch):
        """Values under 1 KB stay inline and need no storage"""
        monkeypatch.delenv("SLAY_STORAGE_BUCKET", raising=False)

        assert slay.put("small").storage_tier == "inline"

    def test_large_value_without_bucket_names_the_variable(self, local_backend, monkeypatch):
        """The error says which variable to set and what to set it to"""
        monkeypatch.delenv("SLAY_STORAGE_BUCKET", raising=False)

        with pytest.raises(StorageError) as excinfo:
            slay.put(LARGE)

        message = str(excinfo.value)
        assert "SLAY_STORAGE_BUCKET is not set" in message
        assert "StorageBucket output" in message
        assert "directory" in message

    def test_error_does_not_suggest_cdk_deploy(self, local_backend, monkeypatch):
        """cdk deploy does not set the variable on clients, so do not suggest it"""
        monkeypatch.delenv("SLAY_STORAGE_BUCKET", raising=False)

        with pytest.raises(StorageError) as excinfo:
            slay.put(LARGE)

        assert "cdk deploy" not in str(excinfo.value)

    def test_large_value_in_local_directory(self, local_backend, mock_s3_bucket):
        """A directory as SLAY_STORAGE_BUCKET stores large values on disk"""
        ref = slay.put(LARGE)

        assert ref.storage_tier == "s3"
        assert (mock_s3_bucket / ref.s3_key).is_file()
        assert slay.get(ref) == LARGE
