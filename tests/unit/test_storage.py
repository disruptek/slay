"""
Unit tests for storage tier selection and the storage bucket setting.
"""

import pytest
import slay
from slay.core.wire import dict_to_ref, ref_to_dict
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


@pytest.fixture(params=["directory", "s3"])
def store(request):
    """SLAY_STORAGE_BUCKET as a directory, then as an S3 bucket in moto"""
    if request.param == "directory":
        return request.getfixturevalue("mock_s3_bucket")
    return request.getfixturevalue("s3_bucket")


class TestObjects:
    """write_object, read_object and object_exists work on any key"""

    def test_write_then_read(self, store):
        from slay.core.storage import read_object, write_object

        write_object("cache/ab/abc.json", b'{"a": 1}')

        assert read_object("cache/ab/abc.json") == b'{"a": 1}'

    def test_read_missing_returns_none(self, store):
        from slay.core.storage import read_object

        assert read_object("cache/zz/missing.json") is None

    def test_exists(self, store):
        from slay.core.storage import object_exists, write_object

        write_object("cache/ab/present.json", b"{}")

        assert object_exists("cache/ab/present.json")
        assert not object_exists("cache/ab/absent.json")

    def test_large_value_round_trip(self, local_backend, store):
        """Values of 1 KB or more go through the same store"""
        ref = slay.put(LARGE)

        assert ref.storage_tier == "s3"
        assert dict_to_ref(ref_to_dict(ref)).get() == LARGE
