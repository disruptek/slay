"""
schedule(), reschedule(), unschedule() and schedules() on the Lambda
backend, with EventBridge Scheduler and S3 in moto. moto keeps schedules
but never runs them, so a test starts slay-retry with a schedule's
Target.Input itself. No AWS calls.
"""

import json

import boto3
import pytest
import slay
import slay.core.context
from botocore.exceptions import ClientError
from executions import run_execution
from slay.backends.lambda_ import LambdaBackend
from slay.core.wire import dict_to_ref, encode_call
from slay.exceptions import RemoteExecutionError, StorageError
from synth import BUCKET, SCHEDULE_GROUP, SCHEDULER_ROLE_ARN, machine_arn

RETRY_ARN = machine_arn("slay-retry")
# The retry settings of a scheduled call without add_retry
ONE_ATTEMPT = {"waits": [], "retry_on": [], "timeout": 900, "dead_letter": True}
LARGE = "x" * 4096


@pytest.fixture
def backend(sfn, stand_in, schedule_group, monkeypatch):
    lambda_backend = LambdaBackend()
    lambda_backend.lambda_client = stand_in
    monkeypatch.setattr(slay.core.context._context, "_backend", lambda_backend)
    return lambda_backend


def shout():
    """A function that pickles to more than 1 KB, so it is stored in S3"""
    pad = "p" * 2048

    def fn(text):
        return text + "!" + pad[:0]

    return fn


def get_schedule(name):
    return boto3.client("scheduler").get_schedule(Name=name, GroupName=SCHEDULE_GROUP)


def schedule_input(name):
    return json.loads(get_schedule(name)["Target"]["Input"])


def keys(prefix):
    listed = boto3.client("s3").list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    return sorted(entry["Key"] for entry in listed.get("Contents", []))


def read(key):
    return boto3.client("s3").get_object(Bucket=BUCKET, Key=key)["Body"].read()


def delete(key):
    boto3.client("s3").delete_object(Bucket=BUCKET, Key=key)


def copy_key(name, ref):
    return f"schedules/{name}/{ref.hash}.bin"


def run_scheduled(sfn, name):
    """Start slay-retry as Scheduler would for this schedule; the response"""
    described = run_execution(sfn, RETRY_ARN, get_schedule(name)["Target"]["Input"])
    assert described["status"] == "SUCCEEDED", described
    return json.loads(described["output"])["response"]


def scheduler_error(code, operation):
    return ClientError({"Error": {"Code": code, "Message": f"{code} in moto"}}, operation)


class RefusingScheduler:
    """A Scheduler client that finds no schedule and refuses to create one"""

    def get_schedule(self, **_):
        raise scheduler_error("ResourceNotFoundException", "GetSchedule")

    def create_schedule(self, **_):
        raise scheduler_error("ValidationException", "CreateSchedule")


class TestSchedule:
    def test_creates_the_schedule(self, backend):
        fn = slay.put(lambda x: x)

        slay.schedule("prices", "rate(1 hour)", fn, 21)

        schedule = get_schedule("prices")
        assert schedule["ScheduleExpression"] == "rate(1 hour)"
        assert schedule["FlexibleTimeWindow"] == {"Mode": "OFF"}
        assert schedule["Target"]["Arn"] == RETRY_ARN
        assert schedule["Target"]["RoleArn"] == SCHEDULER_ROLE_ARN
        assert schedule_input("prices") == {
            "index": 0,
            "call": encode_call(fn, [slay.put(21)], {}),
            "retry": ONE_ATTEMPT,
            "attempt": 0,
        }

    def test_inline_values_need_no_copies(self, backend):
        slay.schedule("prices", "rate(1 hour)", slay.put(lambda x: x), 21)

        assert keys("schedules/") == []

    def test_copies_values_stored_in_s3(self, backend):
        fn = slay.put(shout())
        text = slay.put(LARGE)

        slay.schedule("prices", "rate(1 hour)", fn, text, small=1)

        call = schedule_input("prices")["call"]
        func = dict_to_ref(call["func_ref"])
        (argument,) = [dict_to_ref(wire) for wire in call["args_refs"]]
        assert (func.hash, func.s3_key) == (fn.hash, copy_key("prices", fn))
        assert (argument.hash, argument.s3_key) == (text.hash, copy_key("prices", text))
        assert read(func.s3_key) == read(fn.s3_key)
        assert read(argument.s3_key) == read(text.s3_key)
        assert call["kwargs_refs"]["small"]["storage_tier"] == "inline"
        assert keys("schedules/prices/") == sorted([copy_key("prices", fn), copy_key("prices", text)])

    def test_a_run_uses_the_copies(self, backend, sfn):
        """The originals expire after 90 days; the run reads the copies"""
        fn = slay.put(shout())
        text = slay.put(LARGE)
        slay.schedule("prices", "rate(1 hour)", fn, text)
        delete(fn.s3_key)
        delete(text.s3_key)

        response = run_scheduled(sfn, "prices")

        assert response["status"] == "success", response
        assert dict_to_ref(response["result_ref"]).get() == LARGE + "!"

    def test_inner_function_of_a_wrapper_is_copied(self, backend, sfn):
        inner = slay.put(shout())
        cached = slay.add_caching(inner, version="1")

        slay.schedule("prices", "rate(1 hour)", cached, "a")
        delete(inner.s3_key)

        policy = dict_to_ref(schedule_input("prices")["call"]["func_ref"]).get()
        assert (policy.inner.hash, policy.inner.s3_key) == (inner.hash, copy_key("prices", inner))
        assert policy.version == "1"
        response = run_scheduled(sfn, "prices")
        assert dict_to_ref(response["result_ref"]).get() == "a!"

    def test_add_retry_settings_go_into_the_input(self, backend):
        inner = slay.put(lambda x: x)
        resilient = slay.add_retry(inner, attempts=3, backoff=2, retry_on_timeout=True, timeout=30)

        slay.schedule("prices", "rate(1 hour)", resilient, 1)

        document = schedule_input("prices")
        assert document["call"]["func_ref"]["hash"] == inner.hash
        assert document["retry"] == {
            "waits": [2, 4],
            "retry_on": ["failure", "timeout"],
            "timeout": 30,
            "dead_letter": True,
        }

    def test_name_in_use_raises_and_changes_nothing(self, backend):
        slay.schedule("prices", "rate(1 hour)", slay.put(lambda x: x), 1)

        with pytest.raises(ValueError):
            slay.schedule("prices", "rate(2 hours)", slay.put(shout()), LARGE)

        assert get_schedule("prices")["ScheduleExpression"] == "rate(1 hour)"
        assert keys("schedules/") == []

    def test_directory_bucket_raises(self, backend, tmp_path, monkeypatch):
        fn = slay.put(lambda x: x)
        monkeypatch.setenv("SLAY_STORAGE_BUCKET", str(tmp_path))

        with pytest.raises(StorageError):
            slay.schedule("prices", "rate(1 hour)", fn, 1)

        listed = boto3.client("scheduler").list_schedules(GroupName=SCHEDULE_GROUP)
        assert listed["Schedules"] == []

    def test_refused_schedule_raises_remote_execution_error(self, backend):
        """Scheduler checks the expression; its error is chained"""
        backend.scheduler_client = RefusingScheduler()

        with pytest.raises(RemoteExecutionError) as excinfo:
            slay.schedule("prices", "every hour", slay.put(lambda x: x), 1)

        assert isinstance(excinfo.value.__cause__, ClientError)


class TestReschedule:
    def test_replaces_the_schedule(self, backend):
        fn = slay.put(shout())
        old = slay.put("o" * 4096)
        new = slay.put("n" * 4096)
        slay.schedule("prices", "rate(1 hour)", fn, old)

        slay.reschedule("prices", "rate(6 hours)", fn, new)

        assert get_schedule("prices")["ScheduleExpression"] == "rate(6 hours)"
        (argument,) = schedule_input("prices")["call"]["args_refs"]
        assert argument["hash"] == new.hash

    def test_deletes_the_copies_it_no_longer_uses(self, backend):
        fn = slay.put(shout())
        old = slay.put("o" * 4096)
        new = slay.put("n" * 4096)
        slay.schedule("prices", "rate(1 hour)", fn, old)

        slay.reschedule("prices", "rate(6 hours)", fn, new)

        assert keys("schedules/prices/") == sorted([copy_key("prices", fn), copy_key("prices", new)])

    def test_creates_a_missing_schedule(self, backend):
        slay.reschedule("prices", "rate(6 hours)", slay.put(lambda x: x), 1)

        assert get_schedule("prices")["ScheduleExpression"] == "rate(6 hours)"
        assert schedule_input("prices")["retry"]["dead_letter"] is True


class TestUnschedule:
    def test_deletes_the_schedule_and_its_copies(self, backend):
        slay.schedule("prices", "rate(1 hour)", slay.put(shout()), LARGE)

        slay.unschedule("prices")

        with pytest.raises(ClientError) as excinfo:
            get_schedule("prices")
        assert excinfo.value.response["Error"]["Code"] == "ResourceNotFoundException"
        assert keys("schedules/") == []

    def test_other_schedules_keep_their_copies(self, backend):
        """A name is a whole path segment: unscheduling price spares prices"""
        fn = slay.put(shout())
        slay.schedule("price", "rate(1 hour)", fn, 1)
        slay.schedule("prices", "rate(1 hour)", fn, 1)

        slay.unschedule("price")

        assert keys("schedules/") == [copy_key("prices", fn)]
        assert get_schedule("prices")["Name"] == "prices"

    def test_unknown_name_raises_after_deleting_copies(self, backend):
        boto3.client("s3").put_object(Bucket=BUCKET, Key="schedules/ghost/left.bin", Body=b"x")

        with pytest.raises(ValueError):
            slay.unschedule("ghost")

        assert keys("schedules/") == []


class TestSchedules:
    def test_none(self, backend):
        assert slay.schedules() == []

    def test_sorted_names_of_the_group(self, backend):
        fn = slay.put(lambda x: x)
        slay.schedule("b", "rate(1 hour)", fn, 1)
        slay.schedule("a", "rate(1 hour)", fn, 1)
        boto3.client("scheduler").create_schedule(
            Name="other",
            ScheduleExpression="rate(1 hour)",
            FlexibleTimeWindow={"Mode": "OFF"},
            Target={"Arn": RETRY_ARN, "RoleArn": SCHEDULER_ROLE_ARN},
        )

        assert slay.schedules() == ["a", "b"]
