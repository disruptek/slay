"""
Unit tests for jobs on the local backend: start_apply() runs the items at
once and keeps the outcome in the memory of the process.
"""

import pytest
import slay
from run_log import logged, runs


def fail_odd(x):
    if x % 2:
        raise ValueError(str(x))
    return x


@pytest.fixture
def sleeps(monkeypatch):
    """The seconds passed to time.sleep, which returns at once"""
    recorded = []
    monkeypatch.setattr("time.sleep", recorded.append)
    return recorded


class TestLocalJobs:
    def test_start_apply_returns_a_finished_job(self, local_backend):
        job = slay.start_apply(slay.put(lambda x: x * 2), [1, 2, 3])

        assert isinstance(job, slay.Job)
        assert isinstance(job.id, str) and job.id
        assert job.backend is slay.get_backend()
        assert job.status() == "SUCCEEDED"
        assert slay.get_many(job.results()) == [2, 4, 6]

    def test_items_run_before_start_apply_returns(self, local_backend, mock_s3_bucket, tmp_path):
        """The logged function pickles to more than 1 KB, so it needs the bucket"""
        log = tmp_path / "runs.log"

        slay.start_apply(slay.put(logged(log)), [1, 2])

        assert runs(log) == 2

    def test_attach_finds_the_job_from_a_new_backend(self, local_backend):
        """The job lives in the process, not in the backend object"""
        job = slay.start_apply(slay.put(lambda x: x + 1), [1, 2])
        slay.set_backend("local")

        attached = slay.attach(job.id)

        assert attached.id == job.id
        assert attached.backend is slay.get_backend()
        assert attached.backend is not job.backend
        assert slay.get_many(attached.results()) == [2, 3]

    def test_error_is_kept_for_results(self, local_backend):
        """start_apply() returns; results() raises the first failing item's error"""
        job = slay.start_apply(slay.put(fail_odd), [0, 1, 2, 3])

        assert job.status() == "SUCCEEDED"
        with pytest.raises(ValueError, match="1"):
            job.results()
        with pytest.raises(ValueError, match="1"):
            slay.attach(job.id).results()

    def test_unknown_id_raises(self, local_backend):
        job = slay.attach("no-such-job")

        with pytest.raises(ValueError, match="no-such-job"):
            job.status()
        with pytest.raises(ValueError, match="no-such-job"):
            job.results()

    def test_jobs_have_distinct_ids(self, local_backend):
        fn = slay.put(lambda x: x)

        assert slay.start_apply(fn, [1]).id != slay.start_apply(fn, [1]).id

    def test_retried_function(self, local_backend, mock_s3_bucket, tmp_path, sleeps):
        """add_retry applies to each item of the job"""
        log = tmp_path / "runs.log"
        resilient = slay.add_retry(slay.put(logged(log, fail_times=1)), attempts=2, backoff=0.5)

        job = slay.start_apply(resilient, [1, 2], max_concurrent=1)

        assert slay.get_many(job.results()) == [2, 4]
        assert runs(log) == 3
        assert sleeps == [0.5]

    def test_backend_job_api(self, local_backend):
        """start_many, job_status and job_results on the backend itself"""
        from slay.backends.local import LocalBackend

        backend = LocalBackend()
        fn = slay.put(lambda x: x * 3)
        job_id = backend.start_many(fn, [([slay.put(2)], {})], max_concurrent=4)

        assert backend.job_status(job_id) == "SUCCEEDED"
        assert slay.get_many(backend.job_results(job_id)) == [6]
