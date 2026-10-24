"""
add_retry on the Lambda backend: LambdaBackend starts slay-retry in moto,
whose Task calls the in-process Lambda stand-in. No AWS calls.
"""

import json

import pytest
import slay
from run_log import logged, runs
from lambda_stand_in import function_error
from slay.backends.lambda_ import LambdaBackend
from slay.exceptions import RemoteExecutionError
from sfn_stand_in import EndedExecution
from synth import machine_arn

ARN = machine_arn("slay-retry")


@pytest.fixture
def log(tmp_path):
    return tmp_path / "runs.log"


@pytest.fixture
def backend(sfn, stand_in, monkeypatch):
    """LambdaBackend on moto's Step Functions and STS, used by run() and apply()"""
    lambda_backend = LambdaBackend()
    lambda_backend.lambda_client = stand_in
    monkeypatch.setattr(slay.api, "get_backend", lambda: lambda_backend)
    return lambda_backend


def executions(sfn):
    return sfn.list_executions(stateMachineArn=ARN)["executions"]


def execution_input(sfn):
    (execution,) = executions(sfn)
    described = sfn.describe_execution(executionArn=execution["executionArn"])
    return json.loads(described["input"])


class TestRetriedCalls:
    """A retried call runs as one slay-retry execution"""

    def test_succeeds_after_failures(self, backend, sfn, log):
        resilient = slay.add_retry(slay.put(logged(log, fail_times=2)), attempts=3, backoff=0)

        assert resilient(21).get() == 42
        assert runs(log) == 3
        assert len(executions(sfn)) == 1

    def test_last_failure_raises_original_type(self, backend, sfn, log):
        resilient = slay.add_retry(slay.put(logged(log, fail_times=99)), attempts=2, backoff=0)

        with pytest.raises(ValueError, match="attempt 2") as excinfo:
            resilient(21)

        assert isinstance(excinfo.value.__cause__, RemoteExecutionError)
        assert runs(log) == 2

    def test_timeout_raises_remote_execution_error(self, backend, stand_in, log):
        stand_in.delay = lambda event: 2.5
        resilient = slay.add_retry(slay.put(logged(log)), attempts=1, timeout=1)

        with pytest.raises(RemoteExecutionError) as excinfo:
            resilient(21)

        assert excinfo.value.error_type == "States.Timeout"

    def test_crash_reports_the_lambda_error(self, backend, stand_in, log):
        """A crash payload is reported as a direct FunctionError would be"""
        stand_in.queue.append(
            function_error("Runtime.ExitError", "exit status 137", stack=["  File x\n"])
        )
        resilient = slay.add_retry(slay.put(logged(log)), attempts=1)

        with pytest.raises(RemoteExecutionError) as excinfo:
            resilient(21)

        assert excinfo.value.error_type == "Runtime.ExitError"
        assert excinfo.value.message == "exit status 137"
        assert excinfo.value.remote_traceback == "  File x\n"

    def test_execution_input(self, backend, sfn, log):
        """Waits round up to whole seconds; the classes and timeout follow the options"""
        fn = slay.put(logged(log))
        resilient = slay.add_retry(
            fn, attempts=3, backoff=0.4, retry_on_timeout=True, timeout=30
        )

        resilient(21)

        document = execution_input(sfn)
        assert document["retry"] == {
            "waits": [1, 1],
            "retry_on": ["failure", "timeout"],
            "timeout": 30,
        }
        assert document["attempt"] == 0
        assert document["index"] == 0
        assert document["call"]["func_ref"]["hash"] == fn.hash

    def test_default_input(self, backend, sfn, log):
        """Without a timeout the input holds 900 seconds; only failures retry"""
        slay.add_retry(slay.put(logged(log)))(21)

        assert execution_input(sfn)["retry"] == {
            "waits": [1, 2],
            "retry_on": ["failure"],
            "timeout": 900,
        }

    def test_crash_only(self, backend, sfn, log):
        """retry_on_failure=False leaves failures out of the classes"""
        slay.add_retry(
            slay.put(logged(log)), retry_on_failure=False, retry_on_crash=True
        )(21)

        assert execution_input(sfn)["retry"]["retry_on"] == ["crash"]

    def test_plain_call_invokes_lambda_directly(self, backend, sfn, stand_in, log):
        assert slay.put(logged(log))(21).get() == 42
        assert executions(sfn) == []
        assert len(stand_in.payloads) == 1

    def test_cache_inside_the_executor(self, backend, stand_in, log):
        """add_retry(add_caching(fn)) sends the cache wrapper to the executor"""
        cached = slay.add_caching(slay.put(logged(log, fail_times=1)))
        resilient = slay.add_retry(cached, attempts=2, backoff=0)

        assert resilient(21).get() == 42
        assert resilient(21).get() == 42
        assert runs(log) == 2
        assert {event["func_ref"]["hash"] for event in stand_in.payloads} == {cached.hash}


class TestExecutionStatus:
    """An execution that does not succeed reports its error and cause"""

    @pytest.mark.parametrize("status", ["FAILED", "TIMED_OUT", "ABORTED"])
    def test_reports_error_and_cause(self, backend, log, status):
        backend.sfn_client = EndedExecution(status, error="States.Runtime", cause="bad path")

        with pytest.raises(RemoteExecutionError) as excinfo:
            slay.add_retry(slay.put(logged(log)))(21)

        assert excinfo.value.error_type == "States.Runtime"
        assert excinfo.value.message == "bad path"

    def test_status_without_details(self, backend, log):
        """Without error and cause, the status names the problem"""
        backend.sfn_client = EndedExecution("ABORTED")

        with pytest.raises(RemoteExecutionError, match="ABORTED"):
            slay.add_retry(slay.put(logged(log)))(21)

    def test_state_machine_arn(self, backend, log):
        """The ARN comes from the region and the STS account"""
        backend.sfn_client = EndedExecution("FAILED", error="x", cause="y")

        with pytest.raises(RemoteExecutionError):
            slay.add_retry(slay.put(logged(log)))(21)

        (started,) = backend.sfn_client.started
        assert started["stateMachineArn"] == ARN
        assert started["name"]
