"""
apply(), start_apply() and attach() on the Lambda backend. LambdaBackend
writes the items to the bucket in moto and starts slay-fanout in moto,
whose children call the in-process Lambda stand-in. No AWS calls.
"""

import json

import boto3
import pytest
import slay
from lambda_stand_in import function_error
from run_log import logged, runs
from sfn_stand_in import EndedExecution
from slay.backends.lambda_ import LambdaBackend
from slay.core.wire import dict_to_ref, ref_to_dict
from slay.exceptions import RemoteExecutionError, StorageError
from synth import ACCOUNT, BUCKET, REGION, machine_arn

FANOUT_ARN = machine_arn("slay-fanout")
RETRY_ARN = machine_arn("slay-retry")


def new_backend(stand_in):
    """A LambdaBackend on moto, whose direct invokes call stand_in"""
    backend = LambdaBackend()
    backend.lambda_client = stand_in
    return backend


@pytest.fixture
def backend(fanout, stand_in, monkeypatch):
    """The LambdaBackend that run(), apply(), start_apply() and attach() use"""
    lambda_backend = new_backend(stand_in)
    monkeypatch.setattr(slay.api, "get_backend", lambda: lambda_backend)
    return lambda_backend


@pytest.fixture
def log(tmp_path):
    return tmp_path / "runs.log"


def executions(sfn, arn=FANOUT_ARN):
    return sfn.list_executions(stateMachineArn=arn)["executions"]


def fanout_input(sfn):
    """The name and the input of the one slay-fanout execution"""
    (execution,) = executions(sfn)
    described = sfn.describe_execution(executionArn=execution["executionArn"])
    return execution["name"], json.loads(described["input"])


def read_json(key):
    body = boto3.client("s3").get_object(Bucket=BUCKET, Key=key)["Body"].read()
    return json.loads(body)


def item(ref):
    """An entry of the items file: one call with one positional argument"""
    return {"args_refs": [ref_to_dict(ref)], "kwargs_refs": {}}


def fail_odd(x):
    if x % 2:
        raise ValueError(str(x))
    return x


class TestApply:
    """apply() runs one slay-fanout execution"""

    def test_results_in_item_order_from_one_execution(self, backend, fanout, stand_in):
        refs = slay.apply(slay.put(lambda x: x * 2), [3, 1, 2])

        assert slay.get_many(refs) == [6, 2, 4]
        assert len(executions(fanout)) == 1
        assert executions(fanout, RETRY_ARN) == []
        assert len(stand_in.payloads) == 3

    def test_items_file_and_input(self, backend, fanout):
        """The items file holds the calls; the input names it and the function"""
        fn = slay.put(lambda x: x + 1)
        two = slay.put(2)

        slay.apply(fn, [1, two], max_concurrent=3)

        name, document = fanout_input(fanout)
        assert document["items_key"] == f"fanout/{name}/items.json"
        assert document["func_ref"] == ref_to_dict(fn)
        assert document["max_concurrency"] == 3
        assert document["item_count"] == 2
        assert document["retry"]["waits"] == []
        assert document["retry"]["timeout"] == 900
        assert read_json(document["items_key"]) == [item(slay.put(1)), item(two)]

    def test_no_limit_by_default(self, backend, fanout):
        slay.apply(slay.put(lambda x: x), [1])

        _, document = fanout_input(fanout)
        assert document["max_concurrency"] == 0

    def test_raises_lowest_failing_index(self, backend, stand_in):
        """Item 1's error is raised even when item 3 fails first"""

        def first_argument(event):
            return dict_to_ref(event["args_refs"][0]).get()

        stand_in.delay = lambda event: 0.3 if first_argument(event) == 1 else 0.0

        with pytest.raises(ValueError) as excinfo:
            slay.apply(slay.put(fail_odd), [0, 1, 2, 3])

        assert str(excinfo.value) == "1"
        assert isinstance(excinfo.value.__cause__, RemoteExecutionError)

    def test_lambda_error_of_an_item(self, backend, stand_in):
        """An item whose Lambda crashed raises as a crashed call does"""
        stand_in.queue.append(function_error("Runtime.ExitError", "exit status 137"))

        with pytest.raises(RemoteExecutionError) as excinfo:
            slay.apply(slay.put(lambda x: x), [1])

        assert excinfo.value.error_type == "Runtime.ExitError"
        assert excinfo.value.message == "exit status 137"

    def test_no_items_make_no_calls(self, backend, fanout):
        assert backend.execute_many(slay.put(lambda x: x), []) == []
        assert executions(fanout) == []

    def test_over_add_retry(self, backend, fanout, log):
        """One fan-out whose input holds the retry settings, for the inner function"""
        inner = slay.put(logged(log, fail_times=1))
        resilient = slay.add_retry(inner, attempts=2, backoff=0)

        assert slay.get_many(slay.apply(resilient, [1, 2, 3])) == [2, 4, 6]
        assert runs(log) == 4
        assert executions(fanout, RETRY_ARN) == []
        _, document = fanout_input(fanout)
        assert document["func_ref"]["hash"] == inner.hash
        assert document["retry"] == {"waits": [0], "retry_on": ["failure"], "timeout": 900}

    def test_over_add_caching(self, backend, fanout):
        """The client looks up the cache; only the distinct misses fan out"""
        inner = slay.put(lambda x: x * 2)
        cached = slay.add_caching(inner)
        cached(2)

        assert slay.get_many(slay.apply(cached, [1, 2, 1, 3])) == [2, 4, 2, 6]
        _, document = fanout_input(fanout)
        assert document["func_ref"]["hash"] == inner.hash
        assert document["item_count"] == 2
        assert read_json(document["items_key"]) == [item(slay.put(1)), item(slay.put(3))]

    def test_directory_bucket_raises(self, backend, fanout, tmp_path, monkeypatch):
        """The state machine cannot read a directory, so nothing starts"""
        monkeypatch.setenv("SLAY_STORAGE_BUCKET", str(tmp_path))

        with pytest.raises(StorageError):
            slay.apply(slay.put(lambda x: x), [1])

        assert executions(fanout) == []


class TestExecutionStatus:
    """A fan-out execution that does not succeed, or lacks a result"""

    def test_failed_execution_reports_error_and_cause(self, backend):
        backend.sfn_client = EndedExecution(
            "FAILED", error="States.ExceedToleratedFailureThreshold", cause="a child failed"
        )

        with pytest.raises(RemoteExecutionError) as excinfo:
            slay.apply(slay.put(lambda x: x), [1])

        assert excinfo.value.error_type == "States.ExceedToleratedFailureThreshold"
        assert excinfo.value.message == "a child failed"
        (started,) = backend.sfn_client.started
        assert started["stateMachineArn"] == FANOUT_ARN

    def test_missing_index_raises(self, backend):
        """A result file that lacks an item's output raises for that item"""
        s3 = boto3.client("s3")
        output = {"index": 0, "response": {"status": "success", "result_ref": ref_to_dict(slay.put(5))}}
        s3.put_object(
            Bucket=BUCKET,
            Key="fanout/results/run/SUCCEEDED_0.json",
            Body=json.dumps([{"Output": json.dumps(output), "Status": "SUCCEEDED"}]),
        )
        manifest = {"ResultFiles": {"SUCCEEDED": [{"Key": "fanout/results/run/SUCCEEDED_0.json"}]}}
        s3.put_object(Bucket=BUCKET, Key="fanout/results/run/manifest.json", Body=json.dumps(manifest))
        backend.sfn_client = EndedExecution(
            "SUCCEEDED",
            input=json.dumps({"item_count": 2}),
            output=json.dumps(
                {"ResultWriterDetails": {"Bucket": BUCKET, "Key": "fanout/results/run/manifest.json"}}
            ),
        )

        with pytest.raises(RemoteExecutionError, match="item 1"):
            slay.apply(slay.put(lambda x: x), [1, 2])


class TestJobs:
    """start_apply() starts the fan-out; attach() finds it by execution ARN"""

    def test_start_then_attach_from_another_backend(self, backend, stand_in, monkeypatch):
        stand_in.delay = lambda event: 0.5
        job = slay.start_apply(slay.put(lambda x: x * 2), [1, 2])

        assert isinstance(job, slay.Job)
        assert job.id.startswith(f"arn:aws:states:{REGION}:{ACCOUNT}:execution:slay-fanout:")
        assert job.status() == "RUNNING"

        later = new_backend(stand_in)
        monkeypatch.setattr(slay.api, "get_backend", lambda: later)
        attached = slay.attach(job.id)

        assert attached.id == job.id
        assert attached.backend is later
        assert slay.get_many(attached.results()) == [2, 4]
        assert attached.status() == "SUCCEEDED"

    def test_raising_items_leave_a_succeeded_job(self, backend):
        """The execution finished, so the status is SUCCEEDED; results() raises"""
        job = slay.start_apply(slay.put(fail_odd), [0, 1])

        with pytest.raises(ValueError, match="1"):
            job.results()
        assert job.status() == "SUCCEEDED"

    def test_add_retry_goes_into_the_execution(self, backend, fanout, log):
        inner = slay.put(logged(log, fail_times=1))
        resilient = slay.add_retry(inner, attempts=2, backoff=0)

        job = slay.start_apply(resilient, [1, 2], max_concurrent=2)

        assert slay.get_many(job.results()) == [2, 4]
        assert executions(fanout, RETRY_ARN) == []
        _, document = fanout_input(fanout)
        assert document["func_ref"]["hash"] == inner.hash
        assert document["retry"]["waits"] == [0]
        assert document["max_concurrency"] == 2

    def test_other_wrappers_go_to_the_executors(self, backend, fanout):
        """A cache wrapper is the function of every item; duplicates are not merged"""
        cached = slay.add_caching(slay.put(lambda x: x * 2))

        job = slay.start_apply(cached, [1, 1])

        assert slay.get_many(job.results()) == [2, 2]
        _, document = fanout_input(fanout)
        assert document["func_ref"]["hash"] == cached.hash
        assert document["item_count"] == 2

    def test_status_of_an_ended_execution(self, backend):
        backend.sfn_client = EndedExecution("ABORTED")

        assert slay.attach(FANOUT_ARN.replace("stateMachine", "execution") + ":x").status() == "ABORTED"
