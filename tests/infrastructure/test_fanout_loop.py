"""
The slay-fanout state machine, run in moto's ASL interpreter.

Each test writes an items file to the bucket and starts an execution of
the synthesized definition. Each child runs the retry loop, whose Task
calls the in-process Lambda stand-in. The fixture's ResultWriter writes
the result files as AWS does. No AWS calls.
"""

import gc
import json
import time
import uuid

import boto3
import pytest
import slay
from run_log import logged, runs
from slay.core.wire import dict_to_ref, ref_to_dict
from synth import BUCKET, machine_arn

ARN = machine_arn("slay-fanout")


def read(key):
    return boto3.client("s3").get_object(Bucket=BUCKET, Key=key)["Body"].read()


def run_fanout(sfn, func_ref, items, waits=(), max_concurrency=0):
    """Write the items file, start slay-fanout and wait for it to end"""
    key = f"fanout/{uuid.uuid4().hex}/items.json"
    calls = [{"args_refs": [ref_to_dict(slay.put(item))], "kwargs_refs": {}} for item in items]
    boto3.client("s3").put_object(Bucket=BUCKET, Key=key, Body=json.dumps(calls).encode("utf-8"))
    document = {
        "items_key": key,
        "func_ref": ref_to_dict(func_ref),
        "retry": {"waits": list(waits), "retry_on": ["failure"], "timeout": 900},
        "max_concurrency": max_concurrency,
        "item_count": len(items),
    }
    execution = sfn.start_execution(
        stateMachineArn=ARN, name=uuid.uuid4().hex, input=json.dumps(document)
    )["executionArn"]
    deadline = time.monotonic() + 60
    while True:
        described = sfn.describe_execution(executionArn=execution)
        if described["status"] != "RUNNING":
            return described
        assert time.monotonic() < deadline, "the execution did not end"
        time.sleep(0.05)


def child_outputs(described):
    """The child outputs in the result files, in index order"""
    assert described["status"] == "SUCCEEDED", described
    details = json.loads(described["output"])["ResultWriterDetails"]
    assert details["Bucket"] == BUCKET
    manifest = json.loads(read(details["Key"]))
    outputs = [
        json.loads(entry["Output"])
        for listed in manifest["ResultFiles"]["SUCCEEDED"]
        for entry in json.loads(read(listed["Key"]))
    ]
    return sorted(outputs, key=lambda output: output["index"])


@pytest.fixture
def log(tmp_path):
    return tmp_path / "runs.log"


def fail_odd(x):
    if x % 2:
        raise ValueError(str(x))
    return x


class TestResults:
    """Every child's output reaches the result files"""

    def test_outputs_carry_the_index(self, fanout, stand_in):
        outputs = child_outputs(run_fanout(fanout, slay.put(lambda x: x * 2), [3, 1, 2]))

        assert [output["index"] for output in outputs] == [0, 1, 2]
        values = [dict_to_ref(output["response"]["result_ref"]).get() for output in outputs]
        assert values == [6, 2, 4]

    def test_a_raised_error_is_an_output(self, fanout, stand_in):
        """The loop reports a function's error, so the Map still succeeds"""
        outputs = child_outputs(run_fanout(fanout, slay.put(fail_odd), [0, 1, 2]))

        assert [output["response"]["status"] for output in outputs] == [
            "success",
            "error",
            "success",
        ]
        assert outputs[1]["response"]["error"] == "1"

    def test_each_item_retries_with_the_input(self, fanout, stand_in, log):
        """The retry settings of the input apply to every child"""
        outputs = child_outputs(
            run_fanout(fanout, slay.put(logged(log, fail_times=1)), [1, 2], waits=[0])
        )

        assert [output["response"]["status"] for output in outputs] == ["success", "success"]
        assert runs(log) == 3


class TestConcurrency:
    """max_concurrency in the input limits the children that run at once"""

    DELAY = 0.5
    ITEMS = [0, 1, 2, 3]

    def _elapsed(self, fanout, stand_in, max_concurrency):
        """
        Seconds for the fan-out, without garbage collection: the test
        session's heap holds about a million objects, and one full
        collection there takes about 0.5 seconds, as long as one item.
        """
        stand_in.delay = lambda event: self.DELAY
        gc.collect()
        gc.disable()
        try:
            started = time.monotonic()
            outputs = child_outputs(
                run_fanout(fanout, slay.put(lambda x: x), self.ITEMS, max_concurrency=max_concurrency)
            )
            elapsed = time.monotonic() - started
        finally:
            gc.enable()
        assert len(outputs) == len(self.ITEMS)
        return elapsed

    def test_one_at_a_time(self, fanout, stand_in):
        assert self._elapsed(fanout, stand_in, 1) >= self.DELAY * len(self.ITEMS)

    def test_zero_sets_no_limit(self, fanout, stand_in):
        assert self._elapsed(fanout, stand_in, 0) < self.DELAY * 2
