"""
Unit tests for run_later(), run_at() and the schedule functions on the
local backend: a deferred call runs in the process before it returns, and
the schedule functions are refused.
"""

import datetime as dt

import pytest
import slay
from functions import fail_odd
from run_log import logged, runs
from slay.backends.local import LocalBackend
from slay.exceptions import SlayError

UTC = dt.timezone.utc


@pytest.fixture
def log(tmp_path):
    return tmp_path / "runs.log"


class TestRunLater:
    def test_returns_a_finished_job(self, local_backend):
        job = slay.run_later(slay.put(lambda x: x * 2), 21)

        assert isinstance(job, slay.Job)
        assert isinstance(job.id, str) and job.id
        assert job.backend is slay.get_backend()
        assert job.status() == "SUCCEEDED"
        assert job.result().get() == 42

    def test_runs_before_it_returns(self, local_backend, mock_s3_bucket, log):
        """The logged function pickles to more than 1 KB, so it needs the bucket"""
        slay.run_later(slay.put(logged(log)), 1)

        assert runs(log) == 1

    def test_every_keyword_goes_to_the_function(self, local_backend):
        """fn is positional-only, so the function may take a keyword named fn"""
        both = slay.put(lambda fn, when: [fn, when])

        assert slay.run_later(both, fn=1, when=2).result().get() == [1, 2]

    def test_reference_arguments_pass_through(self, local_backend):
        five = slay.put(5)

        assert slay.run_later(slay.put(lambda x: x * 2), five).result().get() == 10

    def test_error_is_kept_for_result(self, local_backend):
        """run_later() returns; result() raises the function's error"""
        job = slay.run_later(slay.put(fail_odd), 1)

        assert job.status() == "SUCCEEDED"
        with pytest.raises(ValueError, match="1"):
            job.result()
        with pytest.raises(ValueError, match="1"):
            slay.attach(job.id).result()

    def test_attach_finds_the_job_from_a_new_backend(self, local_backend):
        job = slay.run_later(slay.put(lambda x: x + 1), 1)
        slay.set_backend("local")

        attached = slay.attach(job.id)

        assert attached.backend is not job.backend
        assert attached.result().get() == 2

    def test_results_refuses_a_job_of_one_call(self, local_backend):
        job = slay.run_later(slay.put(lambda x: x), 1)

        with pytest.raises(ValueError):
            job.results()

    def test_result_refuses_a_job_of_many_calls(self, local_backend):
        job = slay.start_apply(slay.put(lambda x: x), [1])

        with pytest.raises(ValueError):
            job.result()

    def test_jobs_have_distinct_ids(self, local_backend):
        fn = slay.put(lambda x: x)

        assert slay.run_later(fn, 1).id != slay.run_later(fn, 1).id

    def test_retried_function(self, local_backend, mock_s3_bucket, log, sleeps):
        resilient = slay.add_retry(slay.put(logged(log, fail_times=1)), attempts=2, backoff=0.5)

        assert slay.run_later(resilient, 1).result().get() == 2
        assert runs(log) == 2
        assert sleeps == [0.5]

    def test_backend_api(self, local_backend):
        """start_one and job_result on the backend itself"""
        backend = LocalBackend()
        job_id = backend.start_one(slay.put(lambda x: x * 3), [slay.put(2)], {})

        assert backend.job_status(job_id) == "SUCCEEDED"
        assert backend.job_result(job_id).get() == 6
        with pytest.raises(ValueError):
            backend.job_results(job_id)


class TestRunAt:
    """run_at() sleeps until the time, then runs the call"""

    def test_sleeps_for_a_delay(self, local_backend, sleeps):
        job = slay.run_at(dt.timedelta(seconds=30), slay.put(lambda x: x + 1), 1)

        assert sleeps == [pytest.approx(30, abs=1)]
        assert job.result().get() == 2

    def test_sleeps_until_an_aware_time(self, local_backend, sleeps):
        when = dt.datetime.now(UTC) + dt.timedelta(minutes=5)

        slay.run_at(when, slay.put(lambda x: x), 1)

        assert sleeps == [pytest.approx(300, abs=1)]

    def test_aware_time_in_another_zone(self, local_backend, sleeps):
        east = dt.timezone(dt.timedelta(hours=3))
        when = dt.datetime.now(east) + dt.timedelta(minutes=5)

        slay.run_at(when, slay.put(lambda x: x), 1)

        assert sleeps == [pytest.approx(300, abs=1)]

    def test_naive_time_is_local_time(self, local_backend, sleeps, time_zone):
        """Read as UTC, a naive time in UTC-5 would lie five hours in the past"""
        time_zone("EST5")
        when = dt.datetime.now() + dt.timedelta(minutes=10)

        slay.run_at(when, slay.put(lambda x: x), 1)

        assert sleeps == [pytest.approx(600, abs=1)]

    @pytest.mark.parametrize(
        "when",
        [dt.timedelta(seconds=-5), dt.datetime(2000, 1, 1, tzinfo=UTC)],
        ids=["delay", "time"],
    )
    def test_past_time_runs_at_once(self, local_backend, sleeps, when):
        job = slay.run_at(when, slay.put(lambda x: x + 1), 1)

        assert sleeps == []
        assert job.result().get() == 2

    def test_a_year_ahead_raises(self, local_backend, mock_s3_bucket, log, sleeps):
        """Step Functions stops an execution after a year; nothing runs"""
        with pytest.raises(ValueError):
            slay.run_at(dt.timedelta(days=365), slay.put(logged(log)), 1)

        assert runs(log) == 0
        assert sleeps == []

    def test_just_under_a_year(self, local_backend, sleeps):
        slay.run_at(dt.timedelta(days=364), slay.put(lambda x: x), 1)

        assert sleeps == [pytest.approx(364 * 86400, abs=1)]

    @pytest.mark.parametrize("when", ["tomorrow", 60, None])
    def test_other_types_raise(self, local_backend, when):
        with pytest.raises(TypeError):
            slay.run_at(when, slay.put(lambda x: x), 1)

    def test_every_keyword_goes_to_the_function(self, local_backend, sleeps):
        both = slay.put(lambda when, fn: [when, fn])

        job = slay.run_at(dt.timedelta(0), both, when=1, fn=2)

        assert job.result().get() == [1, 2]

    def test_retry_waits_follow_the_start(self, local_backend, mock_s3_bucket, log, sleeps):
        resilient = slay.add_retry(slay.put(logged(log, fail_times=1)), attempts=2, backoff=0.5)

        job = slay.run_at(dt.timedelta(seconds=10), resilient, 1)

        assert job.result().get() == 2
        assert sleeps == [pytest.approx(10, abs=1), 0.5]


SCHEDULE_CALLS = {
    "schedule": lambda fn: slay.schedule("prices", "rate(1 hour)", fn, 1),
    "reschedule": lambda fn: slay.reschedule("prices", "rate(1 hour)", fn, 1),
    "unschedule": lambda fn: slay.unschedule("prices"),
    "schedules": lambda fn: slay.schedules(),
}


class TestLocalSchedules:
    """No process outlives a schedule, so the local backend refuses them"""

    @pytest.mark.parametrize("call", SCHEDULE_CALLS.values(), ids=SCHEDULE_CALLS.keys())
    def test_refused(self, local_backend, call):
        with pytest.raises(SlayError):
            call(slay.put(lambda x: x))

    @pytest.mark.parametrize("name", ["", "a" * 65, "two words", "a/b", "prix-été"])
    @pytest.mark.parametrize("function", ["schedule", "reschedule"])
    def test_bad_names_raise_value_error(self, local_backend, function, name):
        """The name is checked before the backend sees it"""
        with pytest.raises(ValueError):
            getattr(slay, function)(name, "rate(1 hour)", slay.put(lambda x: x), 1)

    @pytest.mark.parametrize("name", ["a" * 64, "A-z_0.9"])
    def test_good_names_reach_the_backend(self, local_backend, name):
        with pytest.raises(SlayError):
            slay.schedule(name, "rate(1 hour)", slay.put(lambda x: x), 1)

    def test_backend_api(self):
        backend = LocalBackend()
        fn = slay.put(lambda x: x)

        with pytest.raises(SlayError):
            backend.schedule("prices", "rate(1 hour)", fn, [], {})
        with pytest.raises(SlayError):
            backend.unschedule("prices")
        with pytest.raises(SlayError):
            backend.schedule_names()
