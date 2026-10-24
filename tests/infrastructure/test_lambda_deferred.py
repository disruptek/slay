"""
run_later(), run_at() and Job.result() on the Lambda backend.
LambdaBackend starts slay-retry in moto, whose Task calls the in-process
Lambda stand-in. No AWS calls.
"""

import datetime as dt
import json

import pytest
import slay
import slay.core.context
from functions import fail_odd
from lambda_stand_in import function_error
from run_log import logged, runs
from sfn_stand_in import EndedExecution
from slay.backends.lambda_ import LambdaBackend
from slay.core.wire import encode_call, ref_to_dict
from slay.exceptions import RemoteExecutionError
from synth import ACCOUNT, REGION, machine_arn

RETRY_ARN = machine_arn("slay-retry")
RETRY_EXECUTIONS = f"arn:aws:states:{REGION}:{ACCOUNT}:execution:slay-retry:"
FANOUT_EXECUTIONS = f"arn:aws:states:{REGION}:{ACCOUNT}:execution:slay-fanout:"
UTC = dt.timezone.utc

# One attempt with the Lambda's own timeout, and a dead letter if it fails:
# the retry settings of a deferred call without add_retry
ONE_ATTEMPT = {"waits": [], "retry_on": [], "timeout": 900, "dead_letter": True}


def new_backend(stand_in):
    """A LambdaBackend on moto, whose direct invokes call stand_in"""
    backend = LambdaBackend()
    backend.lambda_client = stand_in
    return backend


def use(monkeypatch, backend):
    """Make backend the current backend of every slay function"""
    monkeypatch.setattr(slay.core.context._context, "_backend", backend)


@pytest.fixture
def backend(fanout, stand_in, monkeypatch, utc):
    lambda_backend = new_backend(stand_in)
    use(monkeypatch, lambda_backend)
    return lambda_backend


@pytest.fixture
def log(tmp_path):
    return tmp_path / "runs.log"


def execution_input(sfn, job_id):
    return json.loads(sfn.describe_execution(executionArn=job_id)["input"])


def started_input(backend):
    """The input of the one execution that an EndedExecution stand-in started"""
    (started,) = backend.sfn_client.started
    assert started["stateMachineArn"] == RETRY_ARN
    return json.loads(started["input"])


class TestRunLater:
    def test_starts_slay_retry_and_returns(self, backend, stand_in):
        stand_in.delay = lambda event: 0.5

        job = slay.run_later(slay.put(lambda x: x * 2), 21)

        assert isinstance(job, slay.Job)
        assert job.id.startswith(RETRY_EXECUTIONS)
        assert job.status() == "RUNNING"
        assert job.result().get() == 42
        assert job.status() == "SUCCEEDED"

    def test_input_asks_for_dead_letters(self, backend, fanout):
        fn = slay.put(lambda x, scale: x * scale)
        two = slay.put(2)

        job = slay.run_later(fn, two, scale=3)

        assert job.result().get() == 6

        assert execution_input(fanout, job.id) == {
            "index": 0,
            "call": encode_call(fn, [two], {"scale": slay.put(3)}),
            "retry": ONE_ATTEMPT,
            "attempt": 0,
        }

    def test_attach_from_another_backend(self, backend, stand_in, monkeypatch):
        stand_in.delay = lambda event: 0.5
        job = slay.run_later(slay.put(lambda x: x + 1), 1)

        later = new_backend(stand_in)
        use(monkeypatch, later)
        attached = slay.attach(job.id)

        assert attached.backend is later
        assert attached.result().get() == 2

    def test_raising_call(self, backend):
        """The execution finishes, so the status is SUCCEEDED; result() raises"""
        job = slay.run_later(slay.put(fail_odd), 1)

        with pytest.raises(ValueError, match="1") as excinfo:
            job.result()
        assert isinstance(excinfo.value.__cause__, RemoteExecutionError)
        assert job.status() == "SUCCEEDED"

    def test_raising_call_sends_a_dead_letter(self, backend, fanout, dead_letters):
        fn = slay.put(fail_odd)
        job = slay.run_later(fn, 1)
        with pytest.raises(ValueError):
            job.result()

        (letter,) = dead_letters()
        assert letter["execution"] == job.id
        assert letter["call"] == execution_input(fanout, job.id)["call"]
        assert letter["response"]["error_type"] == "ValueError"

    def test_crash_sends_a_dead_letter(self, backend, stand_in, dead_letters):
        stand_in.queue.append(function_error("Runtime.ExitError", "exit status 137"))
        job = slay.run_later(slay.put(lambda x: x), 1)

        with pytest.raises(RemoteExecutionError) as excinfo:
            job.result()

        assert excinfo.value.error_type == "Runtime.ExitError"
        (letter,) = dead_letters()
        assert letter["response"]["status"] == "lambda_error"

    def test_result_sends_no_dead_letter(self, backend, dead_letters):
        slay.run_later(slay.put(lambda x: x), 1).result()

        assert dead_letters() == []

    def test_retried_run_sends_no_dead_letter(self, backend, log, dead_letters):
        """A retried run() has a caller who gets the error"""
        resilient = slay.add_retry(slay.put(logged(log, fail_times=99)), attempts=1)

        with pytest.raises(ValueError):
            resilient(1)

        assert dead_letters() == []

    def test_add_retry_goes_into_the_execution(self, backend, fanout, log):
        inner = slay.put(logged(log, fail_times=1))
        resilient = slay.add_retry(inner, attempts=2, backoff=0)

        job = slay.run_later(resilient, 1)

        assert job.result().get() == 2
        assert runs(log) == 2
        document = execution_input(fanout, job.id)
        assert document["call"]["func_ref"] == ref_to_dict(inner)
        assert document["retry"] == {
            "waits": [0],
            "retry_on": ["failure"],
            "timeout": 900,
            "dead_letter": True,
        }

    def test_other_wrappers_go_to_the_executor(self, backend, fanout):
        cached = slay.add_caching(slay.put(lambda x: x * 2))

        job = slay.run_later(cached, 1)

        assert job.result().get() == 2
        assert execution_input(fanout, job.id)["call"]["func_ref"]["hash"] == cached.hash


class TestJobKinds:
    """A job ID names its state machine; result() and results() refuse the other kind"""

    def test_results_refuses_a_job_of_one_call(self, backend):
        job = slay.run_later(slay.put(lambda x: x), 1)

        with pytest.raises(ValueError):
            job.results()

    def test_result_refuses_a_job_of_many_calls(self, backend):
        job = slay.start_apply(slay.put(lambda x: x), [1])

        assert job.id.startswith(FANOUT_EXECUTIONS)
        with pytest.raises(ValueError):
            job.result()

    def test_retried_run_is_a_job_of_one_call(self, backend, fanout, log):
        """attach() finds any slay-retry execution, a retried run() among them"""
        slay.add_retry(slay.put(logged(log)), attempts=1)(21)
        (execution,) = fanout.list_executions(stateMachineArn=RETRY_ARN)["executions"]

        assert slay.attach(execution["executionArn"]).result().get() == 42

    @pytest.mark.parametrize("method", ["result", "results"])
    def test_other_state_machines_raise(self, backend, method):
        other = f"arn:aws:states:{REGION}:{ACCOUNT}:execution:other:x"

        with pytest.raises(ValueError):
            getattr(slay.attach(other), method)()

    def test_execution_that_did_not_succeed(self, backend):
        backend.sfn_client = EndedExecution("FAILED", error="States.Runtime", cause="bad path")

        with pytest.raises(RemoteExecutionError) as excinfo:
            slay.attach(RETRY_EXECUTIONS + "x").result()

        assert excinfo.value.error_type == "States.Runtime"
        assert excinfo.value.message == "bad path"


class TestRunAt:
    """run_at() puts the start time into the input, in UTC whole seconds"""

    @pytest.fixture
    def ended(self, backend):
        """Executions that start and record their input, and run nothing"""
        backend.sfn_client = EndedExecution("SUCCEEDED", output="{}")
        return backend

    def test_aware_time(self, ended):
        """A fraction of a second rounds up, so the call never starts early"""
        when = (dt.datetime.now(UTC) + dt.timedelta(hours=2)).replace(microsecond=250000)

        slay.run_at(when, slay.put(lambda x: x), 1)

        expected = (when.replace(microsecond=0) + dt.timedelta(seconds=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        document = started_input(ended)
        assert document["at"] == expected
        assert document["retry"]["dead_letter"] is True

    def test_whole_second_stays(self, ended):
        when = (dt.datetime.now(UTC) + dt.timedelta(hours=2)).replace(microsecond=0)

        slay.run_at(when, slay.put(lambda x: x), 1)

        assert started_input(ended)["at"] == when.strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_aware_time_in_another_zone(self, ended):
        east = dt.timezone(dt.timedelta(hours=3))
        when = (dt.datetime.now(east) + dt.timedelta(hours=2)).replace(microsecond=0)

        slay.run_at(when, slay.put(lambda x: x), 1)

        assert started_input(ended)["at"] == when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_naive_time_is_local_time(self, ended, time_zone):
        time_zone("EST5")
        when = (dt.datetime.now() + dt.timedelta(hours=2)).replace(microsecond=0)

        slay.run_at(when, slay.put(lambda x: x), 1)

        five_hours_later = when + dt.timedelta(hours=5)
        assert started_input(ended)["at"] == five_hours_later.strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_delay(self, ended):
        before = dt.datetime.now(UTC)

        slay.run_at(dt.timedelta(minutes=30), slay.put(lambda x: x), 1)

        after = dt.datetime.now(UTC)
        at = dt.datetime.strptime(started_input(ended)["at"], "%Y-%m-%dT%H:%M:%SZ")
        at = at.replace(tzinfo=UTC)
        assert before + dt.timedelta(minutes=30) - dt.timedelta(seconds=1) <= at
        assert at <= after + dt.timedelta(minutes=30) + dt.timedelta(seconds=1)

    def test_a_year_ahead_raises_before_anything_starts(self, ended):
        with pytest.raises(ValueError):
            slay.run_at(dt.timedelta(days=365), slay.put(lambda x: x), 1)

        assert ended.sfn_client.started == []

    def test_runs_after_the_wait(self, backend, stand_in):
        """In moto, the Wait holds the attempt until the time"""
        when = dt.datetime.now(UTC).replace(microsecond=0) + dt.timedelta(seconds=2)
        invoked = []
        stand_in.delay = lambda event: invoked.append(dt.datetime.now(UTC)) or 0.0

        job = slay.run_at(when, slay.put(lambda x: x * 2), 21)

        assert job.id.startswith(RETRY_EXECUTIONS)
        assert job.result().get() == 42
        assert invoked[0] >= when - dt.timedelta(seconds=1)

    def test_past_time_runs(self, backend):
        job = slay.run_at(dt.timedelta(minutes=-5), slay.put(lambda x: x * 2), 21)

        assert job.result().get() == 42
