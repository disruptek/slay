"""
Unit tests for function composition wrappers.
"""

import pytest
import slay
from run_log import logged, runs
from slay import add_retry, add_caching, create_reference


@pytest.fixture
def sleeps(monkeypatch):
    """The seconds passed to time.sleep, which returns at once"""
    recorded = []
    monkeypatch.setattr("time.sleep", recorded.append)
    return recorded


class TestAddRetry:
    """add_retry on the local backend: an in-process loop"""

    def test_retry_succeeds_immediately(self, local_backend, mock_s3_bucket, tmp_path, sleeps):
        """A first attempt that returns is the only attempt"""
        log = tmp_path / "runs.log"
        retried = add_retry(create_reference(logged(log)), attempts=3)

        assert retried(5).get() == 10
        assert runs(log) == 1
        assert sleeps == []

    def test_retry_with_eventual_success(self, local_backend, mock_s3_bucket, tmp_path, sleeps):
        """Failures wait backoff * 2 ** n seconds, then run again"""
        log = tmp_path / "runs.log"
        retried = add_retry(create_reference(logged(log, fail_times=2)), attempts=5, backoff=0.5)

        assert retried(5).get() == 10
        assert runs(log) == 3
        assert sleeps == [0.5, 1.0]

    def test_retry_gives_up_after_attempts(self, local_backend, mock_s3_bucket, tmp_path, sleeps):
        """After the last attempt, its exception propagates unchanged"""
        log = tmp_path / "runs.log"
        retried = add_retry(create_reference(logged(log, fail_times=99)), attempts=2)

        with pytest.raises(ValueError, match="attempt 2") as excinfo:
            retried(5)

        assert type(excinfo.value) is ValueError
        assert runs(log) == 2
        assert sleeps == [1.0]

    def test_no_retry_without_retry_on_failure(
        self, local_backend, mock_s3_bucket, tmp_path, sleeps
    ):
        """The local loop retries only failures"""
        log = tmp_path / "runs.log"
        retried = add_retry(
            create_reference(logged(log, fail_times=1)),
            retry_on_failure=False,
            retry_on_timeout=True,
            retry_on_crash=True,
        )

        with pytest.raises(ValueError, match="attempt 1"):
            retried(5)
        assert runs(log) == 1

    def test_timeout_does_not_apply_locally(self, local_backend, mock_s3_bucket, tmp_path):
        """The local loop cannot interrupt an attempt, so timeout is ignored"""
        log = tmp_path / "runs.log"
        retried = add_retry(create_reference(logged(log)), timeout=1)

        assert retried(5).get() == 10

    def test_add_retry_does_not_fetch_the_function(self, local_backend, mock_s3_bucket):
        """Wrapping a function that cannot load here does not raise"""
        from slay.core.reference import Reference
        from slay.core.serializer import TypeInfo
        from slay.exceptions import SerializationError

        real = slay.put(lambda x: x)
        foreign = Reference(
            hash=real.hash,
            size=real.size,
            storage_tier=real.storage_tier,
            type_info=TypeInfo(
                format="dill", type_name="function", module="__main__",
                metadata={"python": "3.0"},
            ),
            inline_data=real.inline_data,
        )

        retried = add_retry(foreign, attempts=1)

        with pytest.raises(SerializationError, match="Python 3.0"):
            retried(1)

    def test_wrapper_value_is_callable(self, local_backend, mock_s3_bucket, tmp_path, sleeps):
        """wrapper.get() runs the retried call in this process"""
        log = tmp_path / "runs.log"
        retried = add_retry(create_reference(logged(log, fail_times=1)), backoff=0)

        assert retried.get()(5) == 10
        assert runs(log) == 2

    @pytest.mark.parametrize(
        "options",
        [
            {"attempts": 0},
            {"backoff": -1.0},
            {"timeout": 0},
            {"timeout": 1.5},
        ],
    )
    def test_invalid_options(self, local_backend, options):
        with pytest.raises(ValueError):
            add_retry(slay.put(lambda x: x), **options)


class TestComposedWrappers:
    """Tests for combining multiple wrappers"""

    @staticmethod
    def _logged_double(log):
        path = str(log)

        def double(x):
            with open(path, "a") as f:
                f.write(f"{x}\n")
            return x * 2

        return double

    def test_retry_then_cache(self, local_backend, mock_s3_bucket, tmp_path):
        """A cached retried function runs once and writes a shared entry"""
        log = tmp_path / "runs.log"
        retried = add_retry(create_reference(self._logged_double(log)), attempts=3, backoff=0.01)
        cached = add_caching(retried)

        assert cached(5).get() == 10
        assert cached(5).get() == 10
        assert log.read_text().split() == ["5"]
        assert list((mock_s3_bucket / "cache").rglob("*.json"))

    def test_cache_then_retry(self, local_backend, mock_s3_bucket, tmp_path):
        """A retried cached function runs once and writes a shared entry"""
        log = tmp_path / "runs.log"
        cached = add_caching(create_reference(self._logged_double(log)))
        resilient = add_retry(cached, attempts=3, backoff=0.01)

        assert resilient(5).get() == 10
        assert resilient(5).get() == 10
        assert log.read_text().split() == ["5"]
        assert list((mock_s3_bucket / "cache").rglob("*.json"))
