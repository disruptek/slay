"""
Unit tests for function composition wrappers.
"""

import pytest
import slay
from slay import add_retry, add_caching, create_reference


class TestAddRetry:
    """Tests for add_retry wrapper"""

    def test_retry_succeeds_immediately(self, local_backend, clear_cache, mock_s3_bucket, capsys):
        """Retry succeeds on first attempt without retrying"""

        def always_works(x):
            return x * 2

        func_ref = create_reference(always_works)
        retried = add_retry(func_ref, attempts=3)

        assert retried(5).get() == 10
        assert "[retry]" not in capsys.readouterr().out

    def test_retry_with_eventual_success(self, local_backend, clear_cache, mock_s3_bucket, capsys):
        """Retry succeeds after two failed attempts"""
        attempts = []

        def fails_twice(x):
            attempts.append(x)
            if len(attempts) < 3:
                raise Exception("Simulated failure")
            return x * 2

        func_ref = create_reference(fails_twice)
        retried = add_retry(func_ref, attempts=5, backoff=0.01)

        assert retried(5).get() == 10
        out = capsys.readouterr().out
        assert "Attempt 1 failed" in out
        assert "Attempt 2 failed" in out
        assert "Attempt 3 failed" not in out

    def test_retry_gives_up_after_attempts(self, local_backend, clear_cache, mock_s3_bucket):
        """Retry gives up after max attempts"""

        def always_fails(x):
            raise Exception("Always fails")

        func_ref = create_reference(always_fails)
        retried = add_retry(func_ref, attempts=2, backoff=0.01)

        with pytest.raises(slay.RemoteExecutionError, match="Always fails"):
            retried(5)


class TestAddCaching:
    """Tests for add_caching wrapper"""

    def test_caching_same_arguments(self, local_backend, clear_cache, mock_s3_bucket, capsys):
        """Same arguments return the cached result without running the function"""

        def expensive(x):
            return x * 2

        func_ref = create_reference(expensive)
        cached = add_caching(func_ref)

        assert cached(5).get() == 10
        assert "[cache] Miss" in capsys.readouterr().out

        assert cached(5).get() == 10
        assert "[cache] Hit" in capsys.readouterr().out

    def test_caching_different_arguments(self, local_backend, clear_cache, mock_s3_bucket, capsys):
        """Different arguments cause new execution"""

        def expensive(x):
            return x * 2

        func_ref = create_reference(expensive)
        cached = add_caching(func_ref)

        assert cached(5).get() == 10
        capsys.readouterr()

        assert cached(7).get() == 14
        assert "[cache] Miss" in capsys.readouterr().out

    def test_cache_clear(self, local_backend, clear_cache, mock_s3_bucket):
        """Can clear cache"""

        def expensive(x):
            return x * 2

        func_ref = create_reference(expensive)
        cached = add_caching(func_ref)

        assert cached(5).get() == 10

        # Check cache has entries
        stats = slay.wrappers.cache_stats()
        assert stats["entries"] > 0

        # Clear cache
        slay.wrappers.clear_cache()

        # Cache should be empty
        stats_after = slay.wrappers.cache_stats()
        assert stats_after["entries"] == 0


class TestComposedWrappers:
    """Tests for combining multiple wrappers"""

    def test_retry_then_cache(self, local_backend, clear_cache, mock_s3_bucket, capsys):
        """Combine retry and caching"""

        def flaky(x):
            return x * 2

        func_ref = create_reference(flaky)
        retried = add_retry(func_ref, attempts=3, backoff=0.01)
        cached = add_caching(retried)

        # First call: retry wrapping works, then caches
        assert cached(5).get() == 10
        capsys.readouterr()

        # Second call: from cache
        assert cached(5).get() == 10
        assert "[cache] Hit" in capsys.readouterr().out
