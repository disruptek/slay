"""
Unit tests for the shared result cache: add_caching and add_cache_only.

A function's closure is copied when dill serializes it, so the tests count
runs through a file: each run appends its argument to a log under tmp_path.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import dill
import pytest
import slay
from slay.backends.local import LocalBackend
from slay.core.reference import Reference
from slay.core.serializer import TypeInfo
from slay.exceptions import SerializationError, StorageError

LARGE = "x" * 2000  # serializes to more than the 1 KB inline limit
ROOT = Path(__file__).resolve().parents[2]


def logged(log, body=lambda x: x * x):
    """A function that appends its argument to log, then returns body(x)"""
    path = str(log)

    def fn(x):
        with open(path, "a") as f:
            f.write(f"{x!r}\n")
        return body(x)

    return fn


def runs(log):
    """The arguments logged by logged(log), in run order"""
    return log.read_text().splitlines() if log.exists() else []


class CountingBackend:
    """LocalBackend that counts the calls it receives"""

    name = "counting"
    max_execution_time = None

    def __init__(self):
        self.local = LocalBackend()
        self.executed = []
        self.executed_many = []

    def execute(self, func_ref, args, kwargs, retry=None):
        self.executed.append(func_ref)
        return self.local.execute(func_ref, args, kwargs, retry)

    def execute_many(self, func_ref, args_list, retry=None, max_concurrent=None):
        self.executed_many.append(args_list)
        return self.local.execute_many(func_ref, args_list, retry, max_concurrent)


@pytest.fixture
def backend(monkeypatch, mock_s3_bucket):
    """CountingBackend used by run() and apply(), with a directory bucket"""
    counting = CountingBackend()
    monkeypatch.setattr(slay.api, "get_backend", lambda: counting)
    return counting


@pytest.fixture
def log(tmp_path):
    return tmp_path / "runs.log"


def _entries(bucket):
    """Paths of cache entries under the bucket directory"""
    return sorted((bucket / "cache").rglob("*.json")) if (bucket / "cache").exists() else []


class TestCachedRun:
    """run() over add_caching"""

    def test_hit_skips_backend(self, backend, log):
        """The second call with the same argument reads the entry"""
        cached = slay.add_caching(slay.put(logged(log)))

        assert cached(3).get() == 9
        assert cached(3).get() == 9
        assert runs(log) == ["3"]
        assert len(backend.executed) == 1

    def test_different_arguments_miss(self, backend, log):
        """Each distinct argument runs once"""
        cached = slay.add_caching(slay.put(logged(log)))

        assert cached(3).get() == 9
        assert cached(4).get() == 16
        assert runs(log) == ["3", "4"]
        assert len(_entries(Path(os.environ["SLAY_STORAGE_BUCKET"]))) == 2

    def test_entries_are_shared_with_another_process(
        self, backend, log, mock_s3_bucket, tmp_path
    ):
        """Another process that uses the same bucket hits the entry"""
        cached = slay.add_caching(slay.put(logged(log)))
        cached(3)
        wrapper = tmp_path / "wrapper.dill"
        wrapper.write_bytes(dill.dumps(cached))

        env = dict(os.environ, SLAY_BACKEND="local", PYTHONPATH=str(ROOT / "src"))
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        script = (
            "import sys, dill\n"
            "cached = dill.loads(open(sys.argv[1], 'rb').read())\n"
            "print(cached(3).get())\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script, str(wrapper)],
            env=env, capture_output=True, text=True, timeout=120,
        )

        assert result.returncode == 0, result.stderr[-2000:]
        assert result.stdout.splitlines()[-1] == "9"
        assert runs(log) == ["3"]

    def test_entry_location_and_content(self, backend, log, mock_s3_bucket):
        """The entry is cache/<k[:2]>/<k>.json and holds the result Reference"""
        cached = slay.add_caching(slay.put(logged(log)))
        result = cached(3)

        (entry,) = _entries(mock_s3_bucket)
        assert entry.parent.name == entry.stem[:2]
        assert len(entry.stem) == 64
        assert json.loads(entry.read_text())["hash"] == result.hash

    def test_reference_argument_and_value_share_a_key(self, backend, log):
        """slay.put(3) and 3 have the same hash, so they hit the same entry"""
        cached = slay.add_caching(slay.put(logged(log)))

        cached(slay.put(3))
        assert cached(3).get() == 9
        assert runs(log) == ["3"]
        assert len(_entries(Path(os.environ["SLAY_STORAGE_BUCKET"]))) == 1

    def test_keyword_and_positional_differ(self, backend, log):
        """f(3) and f(x=3) have different keys"""
        cached = slay.add_caching(slay.put(logged(log)))

        cached(3)
        assert cached(x=3).get() == 9
        assert runs(log) == ["3", "3"]
        assert len(_entries(Path(os.environ["SLAY_STORAGE_BUCKET"]))) == 2

    def test_version_separates_entries(self, backend, log):
        """A different version misses; the same version hits"""
        fn = slay.put(logged(log))

        slay.add_caching(fn, version="1")(3)
        slay.add_caching(fn, version="2")(3)
        slay.add_caching(fn, version="1")(3)
        assert runs(log) == ["3", "3"]

    def test_error_writes_no_entry(self, backend, log, mock_s3_bucket):
        """A call that raises is not cached; a call that returns is"""

        def fail_on_3(x):
            if x == 3:
                raise ValueError(f"bad {x}")
            return x

        cached = slay.add_caching(slay.put(logged(log, fail_on_3)))

        cached(2)
        for _ in range(2):
            with pytest.raises(ValueError, match="bad 3"):
                cached(3)
        assert runs(log) == ["2", "3", "3"]
        assert len(_entries(mock_s3_bucket)) == 1

    def test_missing_result_object_is_a_miss(self, backend, log, mock_s3_bucket):
        """A hit whose stored result has expired runs the function again"""
        cached = slay.add_caching(slay.put(logged(log, lambda n: "y" * n)))

        first = cached(2000)
        assert first.storage_tier == "s3"
        (mock_s3_bucket / first.s3_key).unlink()

        assert cached(2000).get() == "y" * 2000
        assert runs(log) == ["2000", "2000"]

    def test_arguments_are_not_stored_again(self, backend, log, mock_s3_bucket):
        """Building the key stores nothing: refs/ holds only the argument,
        the function and the wrapper"""
        fn = slay.put(logged(log, len))
        cached = slay.add_caching(fn)

        assert cached(LARGE).get() == len(LARGE)
        stored = {
            str(path.relative_to(mock_s3_bucket))
            for path in (mock_s3_bucket / "refs").rglob("*.bin")
        }
        expected = {
            ref.s3_key for ref in (slay.put(LARGE), fn, cached) if ref.storage_tier == "s3"
        }
        assert stored == expected

    def test_add_caching_does_not_fetch_the_function(self, backend):
        """Wrapping a function that cannot load here does not raise"""
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

        cached = slay.add_caching(foreign)

        with pytest.raises(SerializationError, match="Python 3.0"):
            cached(1)

    def test_wrapper_value_is_callable(self, backend, log):
        """wrapper.get() runs the cached call in this process"""
        cached = slay.add_caching(slay.put(logged(log)))

        assert cached.get()(3) == 9
        assert cached(3).get() == 9
        assert runs(log) == ["3"]
        assert backend.executed == []

    def test_requires_bucket(self, backend, log, monkeypatch):
        """Without SLAY_STORAGE_BUCKET the call fails before the function runs"""
        cached = slay.add_caching(slay.put(logged(log)))
        monkeypatch.delenv("SLAY_STORAGE_BUCKET")

        with pytest.raises(StorageError, match="SLAY_STORAGE_BUCKET"):
            cached(3)
        assert runs(log) == []
        assert backend.executed == []


class TestCacheOnly:
    """run() over add_cache_only"""

    def test_miss_raises_and_runs_nothing(self, backend, log):
        """A miss raises CacheMiss without a backend call"""
        strict = slay.add_cache_only(slay.put(logged(log)))

        with pytest.raises(slay.CacheMiss):
            strict(3)
        assert runs(log) == []
        assert backend.executed == []

    def test_reads_add_caching_entries(self, backend, log):
        """add_cache_only hits the entries add_caching wrote"""
        fn = slay.put(logged(log))
        slay.add_caching(fn, version="v")(3)

        assert slay.add_cache_only(fn, version="v")(3).get() == 9
        assert runs(log) == ["3"]

    def test_version_must_match(self, backend, log):
        """An entry under another version is a miss"""
        fn = slay.put(logged(log))
        slay.add_caching(fn, version="v")(3)

        with pytest.raises(slay.CacheMiss):
            slay.add_cache_only(fn, version="w")(3)

    def test_missing_result_object_raises(self, backend, log, mock_s3_bucket):
        """An entry whose result has expired is a miss"""
        fn = slay.put(logged(log, lambda n: "y" * n))
        first = slay.add_caching(fn)(2000)
        (mock_s3_bucket / first.s3_key).unlink()

        with pytest.raises(slay.CacheMiss):
            slay.add_cache_only(fn)(2000)

    def test_cache_miss_is_a_slay_error(self):
        assert issubclass(slay.CacheMiss, slay.SlayError)


class TestCachedApply:
    """apply() over a cached function"""

    def test_runs_each_distinct_miss_once(self, backend, log):
        """Hits come from the cache; each distinct miss runs once, in one fan-out"""
        cached = slay.add_caching(slay.put(logged(log)))
        cached(2)

        results = slay.apply(cached, [1, 2, 1, 3])

        assert slay.get_many(results) == [1, 4, 1, 9]
        assert sorted(runs(log)) == ["1", "2", "3"]
        assert [len(args_list) for args_list in backend.executed_many] == [2]

    def test_all_hits_make_no_backend_call(self, backend, log):
        """apply() over cached items makes no backend call"""
        cached = slay.add_caching(slay.put(logged(log)))
        slay.apply(cached, [1, 2])
        backend.executed_many.clear()

        assert slay.get_many(slay.apply(cached, [2, 1])) == [4, 1]
        assert backend.executed_many == []
        assert backend.executed == []

    def test_misses_are_written(self, backend, log):
        """Entries written by apply() serve later run() calls"""
        cached = slay.add_caching(slay.put(logged(log)))
        slay.apply(cached, [1, 2])

        assert cached(2).get() == 4
        assert sorted(runs(log)) == ["1", "2"]
        assert len(_entries(Path(os.environ["SLAY_STORAGE_BUCKET"]))) == 2

    def test_cache_only_names_lowest_missing_index(self, backend, log):
        """apply() over add_cache_only raises CacheMiss for the first miss"""
        fn = slay.put(logged(log))
        slay.add_caching(fn)(1)

        with pytest.raises(slay.CacheMiss, match="item 1"):
            slay.apply(slay.add_cache_only(fn), [1, 5, 6])
        assert runs(log) == ["1"]


class TestWrapperDispatch:
    """A wrapper that reaches a backend applies there"""

    def test_local_backend_executes_wrapper(self, backend, log):
        """LocalBackend.execute runs a wrapper's policy"""
        cached = slay.add_caching(slay.put(logged(log)))

        assert LocalBackend().execute(cached, [slay.put(3)], {}).get() == 9
        assert cached(3).get() == 9
        assert runs(log) == ["3"]
        assert len(_entries(Path(os.environ["SLAY_STORAGE_BUCKET"]))) == 1

    def test_local_backend_executes_wrapper_many(self, backend, log):
        """LocalBackend.execute_many runs a wrapper's policy"""
        cached = slay.add_caching(slay.put(logged(log)))

        results = LocalBackend().execute_many(cached, [([slay.put(3)], {})] * 2)

        assert slay.get_many(results) == [9, 9]
        assert runs(log) == ["3"]
        assert len(_entries(Path(os.environ["SLAY_STORAGE_BUCKET"]))) == 1


class TestS3Cache:
    """The cache in an S3 bucket (moto; no AWS calls)"""

    def test_hit_and_expired_result(self, s3_bucket, monkeypatch, log):
        """Entries live in S3; a deleted result object is a miss"""
        import boto3

        counting = CountingBackend()
        monkeypatch.setattr(slay.api, "get_backend", lambda: counting)
        cached = slay.add_caching(slay.put(logged(log, lambda n: "y" * n)))

        first = cached(2000)
        assert cached(2000).get() == "y" * 2000
        assert runs(log) == ["2000"]

        boto3.client("s3").delete_object(Bucket=s3_bucket, Key=first.s3_key)
        assert cached(2000).get() == "y" * 2000
        assert runs(log) == ["2000", "2000"]
