"""
Schedules on EventBridge Scheduler, for LambdaBackend.

Every schedule lives in the Scheduler group slay. Its target is the
slay-retry state machine, which Scheduler starts through the role
slay-scheduler with the loop input that the schedule holds (see
DESIGN.md, section 12).

A schedule can run for longer than the 90 days that the bucket keeps a
value. The values in S3 that its call names are therefore copied to
schedules/<name>/ in the bucket, which no lifecycle rule covers, and the
call names the copies.
"""

import json
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Optional

from botocore.exceptions import ClientError

from slay.core.reference import Reference, create_reference
from slay.core.storage import open_bucket
from slay.core.wire import encode_call, encode_loop_input
from slay.exceptions import RemoteExecutionError
from slay.wrappers import is_wrapper

if TYPE_CHECKING:
    from slay.core.storage import _S3Store
    from slay.wrappers import Retried

# Names fixed by infrastructure/stacks/compute.py
SCHEDULE_GROUP = "slay"
SCHEDULER_ROLE = "slay-scheduler"

# Where the copies of the schedule <name> live: schedules/<name>/
COPIES = "schedules"


class _NoSuchSchedule(Exception):
    """Scheduler has no schedule of that name"""


class Schedules:
    """
    The schedules of the group slay, through one Scheduler client.

    Attributes:
        client: The boto3 Scheduler client
        target_arn: The ARN of slay-retry
        role_arn: The ARN of the role slay-scheduler
    """

    def __init__(self, client: Any, target_arn: str, role_arn: str):
        self.client = client
        self.target_arn = target_arn
        self.role_arn = role_arn

    def put(
        self,
        name: str,
        expression: str,
        func_ref: Reference,
        args: list[Reference],
        kwargs: dict[str, Reference],
        retry: Optional["Retried"] = None,
        replace_existing: bool = False,
    ) -> None:
        """
        Create the schedule, or with replace_existing replace it, after
        the values it names are copied. A replacement then deletes the
        copies that the new call does not name.

        Raises:
            StorageError: If SLAY_STORAGE_BUCKET does not name an S3 bucket,
                before anything changes
            ValueError: Without replace_existing, if a schedule has the
                name; nothing is copied
            RemoteExecutionError: If Scheduler refuses the request
        """
        bucket = open_bucket()
        if not replace_existing and self._exists(name):
            raise ValueError(f"A schedule named {name} exists; reschedule() replaces it")

        copies = _Copies(bucket, name)
        call = encode_call(
            copies.pin(func_ref),
            [copies.pin(arg) for arg in args],
            {key: copies.pin(value) for key, value in kwargs.items()},
        )
        request = {
            "Name": name,
            "GroupName": SCHEDULE_GROUP,
            "ScheduleExpression": expression,
            "FlexibleTimeWindow": {"Mode": "OFF"},
            "Target": {
                "Arn": self.target_arn,
                "RoleArn": self.role_arn,
                "Input": json.dumps(encode_loop_input(call, retry, dead_letter=True)),
            },
        }

        if not replace_existing:
            self._request("create_schedule", **request)
            return
        try:
            self._request("update_schedule", **request)
        except _NoSuchSchedule:
            self._request("create_schedule", **request)
        bucket.delete([key for key in bucket.keys(copies.prefix) if key not in copies.keys])

    def remove(self, name: str) -> None:
        """
        Delete the schedule, then its copies.

        Raises:
            StorageError: If SLAY_STORAGE_BUCKET does not name an S3 bucket,
                before anything changes
            ValueError: If no schedule has the name, after its copies are
                deleted
            RemoteExecutionError: If Scheduler refuses the request
        """
        bucket = open_bucket()
        try:
            self._request("delete_schedule", Name=name, GroupName=SCHEDULE_GROUP)
            found = True
        except _NoSuchSchedule:
            found = False
        bucket.delete(bucket.keys(_prefix(name)))
        if not found:
            raise ValueError(f"No schedule named {name}")

    def names(self) -> list[str]:
        """The names of the schedules in the group, sorted"""
        names = []
        params = {"GroupName": SCHEDULE_GROUP}
        while True:
            page = self._request("list_schedules", **params)
            names.extend(schedule["Name"] for schedule in page.get("Schedules", []))
            if not page.get("NextToken"):
                return sorted(names)
            params["NextToken"] = page["NextToken"]

    def _exists(self, name: str) -> bool:
        try:
            self._request("get_schedule", Name=name, GroupName=SCHEDULE_GROUP)
        except _NoSuchSchedule:
            return False
        return True

    def _request(self, operation: str, **params: Any) -> dict[str, Any]:
        """
        One Scheduler request.

        Raises:
            _NoSuchSchedule: If the schedule does not exist
            RemoteExecutionError: If the request fails for another reason
        """
        try:
            return getattr(self.client, operation)(**params)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                raise _NoSuchSchedule(params.get("Name")) from e
            raise _scheduler_error(operation, params, e) from e
        except Exception as e:
            raise _scheduler_error(operation, params, e) from e


def _scheduler_error(operation: str, params: dict[str, Any], error: Exception) -> RemoteExecutionError:
    subject = f" for {params['Name']}" if "Name" in params else ""
    return RemoteExecutionError(message=f"Scheduler {operation} failed{subject}: {error}")


def _prefix(name: str) -> str:
    return f"{COPIES}/{name}/"


class _Copies:
    """
    The copies of one schedule's values, under schedules/<name>/.

    Attributes:
        prefix: schedules/<name>/
        keys: The keys of the copies that pin() returned
    """

    def __init__(self, bucket: "_S3Store", name: str):
        self.bucket = bucket
        self.prefix = _prefix(name)
        self.keys: set[str] = set()

    def pin(self, ref: Reference) -> Reference:
        """
        A Reference to the same value that lasts. An inline Reference is
        returned as it is. A Reference stored in S3 is copied, without a
        download, to schedules/<name>/<hash>.bin and returned with that
        key. A wrapper's inner function is pinned first; the wrapper with
        the pinned inner is a new value, which is then pinned.
        """
        if is_wrapper(ref):
            policy = ref.get()
            ref = create_reference(replace(policy, inner=self.pin(policy.inner)))
        if ref.storage_tier != "s3":
            return ref

        key = f"{self.prefix}{ref.hash}.bin"
        # S3 refuses to copy an object onto itself
        if key not in self.keys and ref.s3_key != key:
            self.bucket.copy(ref.s3_key, key)
        self.keys.add(key)
        return replace(ref, s3_key=key)
