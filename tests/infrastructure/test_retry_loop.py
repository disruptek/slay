"""
The slay-retry state machine, run in moto's ASL interpreter.

Each test starts an execution of the synthesized definition. Its Task
calls the in-process Lambda stand-in, which runs the real handler, so the
loop, the handler and the wire format run together without AWS.
"""

import datetime as dt
import json
import time

import pytest
import slay
from botocore.exceptions import ClientError
from executions import run_execution
from lambda_stand_in import function_error
from run_log import logged, runs
from slay.core.wire import dict_to_ref, encode_call
from synth import machine_arn

ARN = machine_arn("slay-retry")


def run_loop(
    sfn, call, waits=(), retry_on=("failure",), timeout=900, index=0, dead_letter=None, **extra
):
    """
    Start slay-retry with this input, the retry settings' dead_letter if
    given, and the extra fields (at), and wait for it to end. The
    description holds the executionArn.
    """
    retry = {"waits": list(waits), "retry_on": list(retry_on), "timeout": timeout}
    if dead_letter is not None:
        retry["dead_letter"] = dead_letter
    document = {"index": index, "call": call, "retry": retry, "attempt": 0, **extra}
    return run_execution(sfn, ARN, json.dumps(document))


def output_of(described, index=0):
    """
    The response in an execution's output. The loop always lets the
    execution succeed, and its output is {index, response}.
    """
    assert described["status"] == "SUCCEEDED", described
    output = json.loads(described["output"])
    assert set(output) == {"index", "response"}, output
    assert output["index"] == index
    return output["response"]


@pytest.fixture
def log(tmp_path):
    return tmp_path / "runs.log"


def double_call(log, fail_times=0):
    return encode_call(slay.put(logged(log, fail_times)), [slay.put(21)], {})


class TestFailures:
    """Class failure: the function raised"""

    def test_result_on_first_attempt(self, sfn, stand_in, log):
        output = output_of(run_loop(sfn, double_call(log)))

        assert output["status"] == "success"
        assert dict_to_ref(output["result_ref"]).get() == 42
        assert runs(log) == 1

    def test_retried_until_success(self, sfn, stand_in, log):
        """Each failure waits, then runs again; the response is the handler's alone"""
        output = output_of(run_loop(sfn, double_call(log, fail_times=2), waits=[0, 0]))

        assert output["status"] == "success"
        assert dict_to_ref(output["result_ref"]).get() == 42
        assert set(output) == {"status", "result_ref", "logs"}
        assert runs(log) == 3

    def test_attempts_end_with_the_last_error(self, sfn, stand_in, log):
        """After the last wait, the last attempt's error response is the output"""
        output = output_of(run_loop(sfn, double_call(log, fail_times=99), waits=[0, 0]))

        assert output["status"] == "error"
        assert output["error_type"] == "ValueError"
        assert output["error"] == "attempt 3"
        assert runs(log) == 3

    def test_not_retried_unless_listed(self, sfn, stand_in, log):
        output = output_of(
            run_loop(sfn, double_call(log, fail_times=1), waits=[0], retry_on=["timeout"])
        )

        assert output["status"] == "error"
        assert runs(log) == 1

    def test_wait_comes_from_the_input(self, sfn, stand_in, log):
        """The loop waits the listed seconds before the next attempt"""
        started = time.monotonic()
        output = output_of(run_loop(sfn, double_call(log, fail_times=1), waits=[1]))

        assert output["status"] == "success"
        assert time.monotonic() - started >= 1.0


class TestTimeouts:
    """Class timeout: the attempt outlasted its timeout, or the Lambda's"""

    @staticmethod
    def _slow_first_attempt(stand_in, seconds=2.5):
        attempts = []

        def delay(event):
            attempts.append(event)
            return seconds if len(attempts) == 1 else 0.0

        stand_in.delay = delay

    def test_retried_when_listed(self, sfn, stand_in, log):
        self._slow_first_attempt(stand_in)

        output = output_of(
            run_loop(sfn, double_call(log), waits=[0], retry_on=["timeout"], timeout=1)
        )

        assert output["status"] == "success"
        assert len(stand_in.payloads) == 2

    def test_reported_when_not_listed(self, sfn, stand_in, log):
        self._slow_first_attempt(stand_in)

        output = output_of(run_loop(sfn, double_call(log), waits=[0], timeout=1))

        assert output["status"] == "lambda_error"
        assert output["errorType"] == "States.Timeout"
        assert len(stand_in.payloads) == 1

    def test_lambda_timeout_is_a_timeout(self, sfn, stand_in, log):
        """Sandbox.Timedout, the Lambda's own timeout, has class timeout"""
        stand_in.queue.append(function_error("Sandbox.Timedout", "Task timed out"))

        output = output_of(run_loop(sfn, double_call(log), waits=[0], retry_on=["timeout"]))

        assert output["status"] == "success"


class TestCrashes:
    """Class crash: the Lambda failed outside the handler"""

    def test_reported_when_not_listed(self, sfn, stand_in, log):
        """The output carries the Lambda error name and its payload"""
        stand_in.queue.append(function_error("Runtime.ExitError", "exit status 137"))

        output = output_of(
            run_loop(sfn, double_call(log), waits=[0], retry_on=["failure", "timeout"])
        )

        assert output["status"] == "lambda_error"
        assert output["errorType"] == "Runtime.ExitError"
        assert json.loads(output["errorMessage"])["errorMessage"] == "exit status 137"
        assert runs(log) == 0

    def test_retried_when_listed(self, sfn, stand_in, log):
        stand_in.queue.append(function_error("Runtime.ExitError", "exit status 137"))

        output = output_of(run_loop(sfn, double_call(log), waits=[0], retry_on=["crash"]))

        assert output["status"] == "success"
        assert runs(log) == 1


class TestThrottling:
    """Lambda throttling is retried by the Task, not counted as an attempt"""

    def test_throttled_invoke_is_retried(self, sfn, stand_in, log):
        throttled = ClientError(
            {
                "Error": {"Code": "TooManyRequestsException", "Message": "Rate exceeded"},
                "ResponseMetadata": {"HTTPStatusCode": 429, "RequestId": "throttled"},
            },
            "Invoke",
        )
        stand_in.queue.append(throttled)

        output = output_of(run_loop(sfn, double_call(log), retry_on=[]))

        assert output["status"] == "success"
        assert len(stand_in.payloads) == 2


class TestIndex:
    """The output carries the input's index, so a fan-out can order its results"""

    def test_index_of_a_result(self, sfn, stand_in, log):
        output = output_of(run_loop(sfn, double_call(log), index=7), index=7)

        assert output["status"] == "success"

    def test_index_survives_retries(self, sfn, stand_in, log):
        """Next attempt keeps the index"""
        output = output_of(
            run_loop(sfn, double_call(log, fail_times=1), waits=[0], index=7), index=7
        )

        assert output["status"] == "success"
        assert runs(log) == 2

    def test_index_of_a_lambda_error(self, sfn, stand_in, log):
        stand_in.queue.append(function_error("Runtime.ExitError", "exit status 137"))

        output = output_of(run_loop(sfn, double_call(log), index=7), index=7)

        assert output["status"] == "lambda_error"


def timestamp(when):
    """A UTC time as the input's at: whole seconds, with a Z"""
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TestDelay:
    """With at, the first attempt waits for that time"""

    def test_first_attempt_waits(self, sfn, stand_in, log, utc):
        """moto rounds the wait down to whole seconds, hence the second of slack"""
        when = dt.datetime.now(dt.timezone.utc).replace(microsecond=0) + dt.timedelta(seconds=2)
        invoked = []
        stand_in.delay = lambda event: invoked.append(time.time()) or 0.0

        output = output_of(run_loop(sfn, double_call(log), at=timestamp(when)))

        assert output["status"] == "success"
        assert invoked[0] >= when.timestamp() - 1.0

    def test_past_time_runs_at_once(self, sfn, stand_in, log, utc):
        when = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
        started = time.monotonic()

        output = output_of(run_loop(sfn, double_call(log), at=timestamp(when)))

        assert output["status"] == "success"
        assert time.monotonic() - started < 5


class TestDeadLetters:
    """With dead_letter true, a last attempt that failed sends a message"""

    def test_raised(self, sfn, stand_in, log, dead_letters):
        call = double_call(log, fail_times=99)

        described = run_loop(sfn, call, dead_letter=True)

        response = output_of(described)
        assert response["status"] == "error"
        assert dead_letters() == [
            {"execution": described["executionArn"], "call": call, "response": response}
        ]

    def test_crashed(self, sfn, stand_in, log, dead_letters):
        """The message's response is the lambda_error report of the output"""
        stand_in.queue.append(function_error("Runtime.ExitError", "exit status 137"))
        call = double_call(log)

        described = run_loop(sfn, call, dead_letter=True)

        response = output_of(described)
        assert response["status"] == "lambda_error"
        assert dead_letters() == [
            {"execution": described["executionArn"], "call": call, "response": response}
        ]

    def test_after_the_last_retry(self, sfn, stand_in, log, dead_letters):
        """Attempts that are retried send nothing; the last one sends one message"""
        output_of(run_loop(sfn, double_call(log, fail_times=99), waits=[0, 0], dead_letter=True))

        assert runs(log) == 3
        (letter,) = dead_letters()
        assert letter["response"]["error"] == "attempt 3"

    def test_none_for_a_result(self, sfn, stand_in, log, dead_letters):
        output = output_of(run_loop(sfn, double_call(log), dead_letter=True))

        assert output["status"] == "success"
        assert dead_letters() == []

    def test_none_for_a_result_after_retries(self, sfn, stand_in, log, dead_letters):
        output_of(run_loop(sfn, double_call(log, fail_times=1), waits=[0], dead_letter=True))

        assert dead_letters() == []

    @pytest.mark.parametrize("extra", [{}, {"dead_letter": False}], ids=["absent", "false"])
    def test_none_without_the_flag(self, sfn, stand_in, log, dead_letters, extra):
        output = output_of(run_loop(sfn, double_call(log, fail_times=99), **extra))

        assert output["status"] == "error"
        assert dead_letters() == []
