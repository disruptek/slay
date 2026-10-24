"""
AWS Lambda execution backend.

Executes functions on AWS Lambda for scalable, serverless compute.
Supports unlimited parallelism up to AWS account limits.

A plain call invokes the slay-executor Lambda directly. A retried call
starts an execution of the slay-retry state machine, whose loop invokes
the Lambda once per attempt, and polls the execution until it ends. Many
calls are one execution of slay-fanout: the client writes the calls to
the storage bucket, and a Distributed Map runs each through the same loop
and writes the outputs back.
"""

import json
import math
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any, Optional

import boto3

from slay.backends.base import ExecutorBackend
from slay.core.reference import Reference
from slay.core.storage import open_bucket
from slay.core.wire import dict_to_ref, encode_arguments, encode_call, ref_to_dict
from slay.exceptions import RemoteExecutionError

if TYPE_CHECKING:
    from slay.core.storage import _S3Store
    from slay.wrappers import Retried

# Names fixed by infrastructure/stacks/compute.py
EXECUTOR_FUNCTION = "slay-executor"
RETRY_STATE_MACHINE = "slay-retry"
FANOUT_STATE_MACHINE = "slay-fanout"

# Seconds per attempt when add_retry sets no timeout: more than the
# Lambda's 14-minute timeout, so the Lambda's own limit ends an attempt
DEFAULT_ATTEMPT_TIMEOUT = 900

# Seconds between DescribeExecution calls: the first wait, and the longest
FIRST_POLL = 0.25
LAST_POLL = 5.0


class LambdaBackend(ExecutorBackend):
    """Execute functions on AWS Lambda"""

    def __init__(self):
        self.lambda_client = boto3.client("lambda")
        self.sfn_client = boto3.client("stepfunctions")
        self.sts_client = boto3.client("sts")
        self.function_name = EXECUTOR_FUNCTION
        self.region = self.sfn_client.meta.region_name
        self._account: Optional[str] = None
        self._account_lock = threading.Lock()

    @property
    def name(self) -> str:
        return "lambda"

    @property
    def max_execution_time(self) -> Optional[int]:
        return 900  # 15 minutes

    def execute(
        self,
        func_ref: Reference,
        args: list[Reference],
        kwargs: dict[str, Reference],
        retry: Optional["Retried"] = None,
    ) -> Reference:
        """
        Execute on AWS Lambda.

        Args:
            func_ref: Reference to function
            args: List of argument references
            kwargs: Keyword argument names mapped to references
            retry: A Retried policy, which runs the call in the slay-retry
                state machine, or None for one direct invocation

        Returns:
            Reference to result

        Raises:
            Exception: The exception the function raised, of its own type.
                Its __cause__ is a RemoteExecutionError that holds the
                remote traceback, the captured output and the type name.
            SlayError: A slay error raised in the handler (for example a
                SerializationError for a Python version mismatch), with the
                same __cause__
            RemoteExecutionError: If the exception cannot be serialized on
                Lambda or loaded here; if the Lambda fails outside the
                handler (error_type is the Lambda errorType, for example
                Sandbox.Timedout); if the last attempt of a retried call
                timed out (error_type States.Timeout) or crashed; if the
                execution ends in another status than SUCCEEDED; or if the
                invocation fails
        """
        payload = encode_call(func_ref, args, kwargs)
        if retry is None:
            response = self._invoke(payload)
        else:
            response = self._run_retry_loop(payload, retry)
        return _result(response)

    def execute_many(
        self,
        func_ref: Reference,
        args_list: list[tuple[list[Reference], dict[str, Reference]]],
        retry: Optional["Retried"] = None,
        max_concurrent: Optional[int] = None,
    ) -> list[Reference]:
        """
        Execute many calls in one slay-fanout execution and wait for it.
        No calls make no AWS request.

        Args:
            func_ref: Reference to function
            args_list: List of (args_refs, kwargs_refs) tuples
            retry: A Retried policy for every call, as in execute()
            max_concurrent: The most calls at once, or None for no limit
                of slay's own

        Returns:
            List of result references in same order

        Raises:
            Exception: The error of the lowest failing index, as execute()
                raises it. Every item runs to completion first.
            StorageError: If SLAY_STORAGE_BUCKET does not name an S3 bucket
            RemoteExecutionError: As job_results raises it
        """
        if not args_list:
            return []
        return self.job_results(self.start_many(func_ref, args_list, retry, max_concurrent))

    def start_many(
        self,
        func_ref: Reference,
        args_list: list[tuple[list[Reference], dict[str, Reference]]],
        retry: Optional["Retried"] = None,
        max_concurrent: Optional[int] = None,
    ) -> str:
        """
        Write the calls to fanout/<name>/items.json in the storage bucket
        and start slay-fanout on them. Returns the execution ARN.

        Raises:
            StorageError: If SLAY_STORAGE_BUCKET does not name an S3 bucket,
                or the items file cannot be written; nothing starts
            RemoteExecutionError: If the execution cannot start
        """
        name = uuid.uuid4().hex
        items_key = f"fanout/{name}/items.json"
        items = [encode_arguments(args, kwargs) for args, kwargs in args_list]
        open_bucket().put(items_key, json.dumps(items).encode("utf-8"))
        document = {
            "items_key": items_key,
            "func_ref": ref_to_dict(func_ref),
            "retry": _retry_input(retry),
            "max_concurrency": max_concurrent or 0,
            "item_count": len(args_list),
        }
        return self._start(FANOUT_STATE_MACHINE, name, document)

    def job_status(self, job_id: str) -> str:
        """
        The status of the job's execution.

        Raises:
            RemoteExecutionError: If the execution cannot be described
        """
        return self._describe(job_id)["status"]

    def job_results(self, job_id: str) -> list[Reference]:
        """
        Wait for the job's execution, then read the results the Map wrote
        and decode them in item order.

        Raises:
            Exception: The error of the lowest failing index, as execute()
                raises it
            RemoteExecutionError: If the execution ends in another status
                than SUCCEEDED, or an item has no result
            StorageError: If a result file cannot be read
        """
        described = self._wait_for(job_id)
        output = _succeeded_output(job_id, described)
        count = json.loads(described["input"])["item_count"]

        bucket = open_bucket()
        manifest = _read_json(bucket, output["ResultWriterDetails"]["Key"])
        responses = {}
        for listed in manifest["ResultFiles"]["SUCCEEDED"]:
            for entry in _read_json(bucket, listed["Key"]):
                child = json.loads(entry["Output"])
                responses[child["index"]] = child["response"]

        results = []
        for index in range(count):
            if index not in responses:
                raise RemoteExecutionError(message=f"{job_id} has no result for item {index}")
            results.append(_result(responses[index]))
        return results

    def _invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        One direct invocation; returns the handler's response.

        Raises:
            RemoteExecutionError: If the invocation fails, its response does
                not parse, or the Lambda failed outside the handler
        """
        try:
            response = self.lambda_client.invoke(
                FunctionName=self.function_name,
                InvocationType="RequestResponse",
                Payload=json.dumps(payload),
            )
        except Exception as e:
            raise RemoteExecutionError(message=f"Failed to invoke Lambda: {e}") from e

        try:
            body = json.loads(response["Payload"].read())
        except Exception as e:
            raise RemoteExecutionError(message=f"Failed to parse Lambda response: {e}") from e

        # The Lambda failed outside the handler; the payload is not ours
        if response.get("FunctionError"):
            raise _function_error(body)
        return body

    def _run_retry_loop(self, payload: dict[str, Any], retry: "Retried") -> dict[str, Any]:
        """
        Run the call in slay-retry; returns the loop's response: the
        handler's response, or a lambda_error document.

        Raises:
            RemoteExecutionError: If the execution cannot start, cannot be
                described, or ends in another status than SUCCEEDED
        """
        document = {"index": 0, "call": payload, "retry": _retry_input(retry), "attempt": 0}
        execution = self._start(RETRY_STATE_MACHINE, uuid.uuid4().hex, document)
        return _succeeded_output(execution, self._wait_for(execution))["response"]

    def _start(self, machine: str, name: str, document: dict[str, Any]) -> str:
        """
        Start an execution of one of slay's state machines; returns its ARN.

        Raises:
            RemoteExecutionError: If the execution cannot start
        """
        try:
            return self.sfn_client.start_execution(
                stateMachineArn=self._state_machine_arn(machine),
                name=name,
                input=json.dumps(document),
            )["executionArn"]
        except Exception as e:
            raise RemoteExecutionError(message=f"Failed to start {machine}: {e}") from e

    def _describe(self, execution: str) -> dict[str, Any]:
        try:
            return self.sfn_client.describe_execution(executionArn=execution)
        except Exception as e:
            raise RemoteExecutionError(message=f"Failed to describe {execution}: {e}") from e

    def _wait_for(self, execution: str) -> dict[str, Any]:
        """Poll DescribeExecution until the execution is no longer RUNNING"""
        delay = FIRST_POLL
        while True:
            described = self._describe(execution)
            if described["status"] != "RUNNING":
                return described
            time.sleep(delay)
            delay = min(delay * 2, LAST_POLL)

    def _state_machine_arn(self, machine: str) -> str:
        """A slay state machine's ARN, from the region and the account (STS, once)"""
        with self._account_lock:
            if self._account is None:
                self._account = self.sts_client.get_caller_identity()["Account"]
        return f"arn:aws:states:{self.region}:{self._account}:stateMachine:{machine}"


def _retry_input(retry: Optional["Retried"]) -> dict[str, Any]:
    """
    The retry part of the loop's input; Wait takes whole seconds. Without
    a policy, one attempt with the Lambda's own timeout.
    """
    if retry is None:
        return {"waits": [], "retry_on": [], "timeout": DEFAULT_ATTEMPT_TIMEOUT}
    return {
        "waits": [math.ceil(wait) for wait in retry.waits()],
        "retry_on": retry.retry_on(),
        "timeout": retry.timeout or DEFAULT_ATTEMPT_TIMEOUT,
    }


def _succeeded_output(execution: str, described: dict[str, Any]) -> dict[str, Any]:
    """
    The output of an ended execution.

    Raises:
        RemoteExecutionError: If it ended in another status than SUCCEEDED,
            with the execution's error and cause
    """
    if described["status"] != "SUCCEEDED":
        raise RemoteExecutionError(
            message=described.get("cause") or f"{execution} ended {described['status']}",
            error_type=described.get("error"),
        )
    return json.loads(described["output"])


def _read_json(bucket: "_S3Store", key: str) -> Any:
    """
    A JSON object that the fan-out wrote to the bucket.

    Raises:
        RemoteExecutionError: If the key does not exist
        StorageError: If the read fails
    """
    data = bucket.get(key)
    if data is None:
        raise RemoteExecutionError(message=f"{key} is missing from the storage bucket")
    return json.loads(data)


def _result(response: dict[str, Any]) -> Reference:
    """
    The result Reference of a handler's response or a loop's output, or
    the error it reports.
    """
    status = response.get("status")

    # The last attempt of a retried call timed out or crashed
    if status == "lambda_error":
        raise _loop_error(response)

    # The function raised; re-raise its exception if it crossed the wire
    if status == "error":
        report = RemoteExecutionError(
            message=response.get("error", "Unknown error"),
            traceback=response.get("traceback"),
            logs=response.get("logs"),
            error_type=response.get("error_type"),
        )
        original = _load_exception(response.get("exception_ref"))
        if original is None:
            raise report
        raise original from report

    try:
        return dict_to_ref(response["result_ref"])
    except Exception as e:
        raise RemoteExecutionError(
            message=f"Failed to reconstruct result: {e}",
            logs=response.get("logs"),
        ) from e


def _load_exception(wire: Optional[dict[str, Any]]) -> Optional[Exception]:
    """The exception behind a handler's exception_ref, or None if unusable"""
    if wire is None:
        return None
    try:
        loaded = dict_to_ref(wire).get()
    except Exception:
        return None
    return loaded if isinstance(loaded, Exception) else None


def _function_error(payload: dict[str, Any]) -> RemoteExecutionError:
    """The error for a FunctionError payload: errorType, errorMessage, stackTrace"""
    stack = payload.get("stackTrace")
    if isinstance(stack, list):
        stack = "".join(stack) or None
    return RemoteExecutionError(
        message=payload.get("errorMessage", "Unknown error"),
        traceback=stack,
        error_type=payload.get("errorType"),
    )


def _loop_error(output: dict[str, Any]) -> RemoteExecutionError:
    """
    The error for a loop whose last attempt timed out or crashed. A cause
    that holds a Lambda error payload is reported as a FunctionError is.
    """
    cause = output.get("errorMessage")
    try:
        payload = json.loads(cause)
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict) and "errorType" in payload:
        return _function_error(payload)
    return RemoteExecutionError(message=cause or "Unknown error", error_type=output.get("errorType"))
