"""
Calls that run later, at a time, or on a schedule.

- run_later(fn, /, *args, **kwargs): start one call and return its Job
- run_at(when, fn, /, *args, **kwargs): the same, from a given time on
- schedule(name, expression, fn, /, *args, **kwargs): run the call on an
  EventBridge Scheduler expression; reschedule() replaces the schedule,
  unschedule() deletes it, and schedules() lists the names

The parameters before / are positional-only, so every keyword argument
goes to the function. On Lambda each call, and each run of a schedule, is
one slay-retry execution, which sends a dead letter when its last attempt
fails (see DESIGN.md, section 12).
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Union

from slay.core.context import get_backend
from slay.core.reference import Reference, as_reference
from slay.jobs import Job
from slay.observability.logger import get_logger
from slay.wrappers import without_retry

logger = get_logger()

# Step Functions stops an execution after a year; run_at() refuses this far
LONGEST_DELAY = timedelta(days=365)

# A schedule name, as EventBridge Scheduler allows it
SCHEDULE_NAME = re.compile(r"[0-9A-Za-z_.-]{1,64}")


def run_later(fn: Reference, /, *args: Any, **kwargs: Any) -> Job:
    """
    Start a call of fn and return its Job at once.

    The backend gets the call as start_apply() gives it the items: for a
    function from add_retry, the inner function and the retry policy; any
    other wrapper runs inside the executor. On Lambda, the call is one
    slay-retry execution, and the client may exit while it runs.

    Returns:
        The Job; job.result() waits, then returns or raises as run() does

    Raises:
        StorageError: If an argument of 1 KB or more cannot be stored
        RemoteExecutionError: If the call cannot start

    Example:
        >>> job = slay.run_later(slay.put(lambda x: x * 2), 21)
        >>> slay.attach(job.id).result().get()
        42
    """
    return _start(fn, args, kwargs, at=None)


def run_at(when: Union[datetime, timedelta], fn: Reference, /, *args: Any, **kwargs: Any) -> Job:
    """
    Start a call of fn whose first attempt waits until when, and return
    its Job at once. On the local backend, sleep until then and run it.

    Args:
        when: A datetime, or a timedelta from now. A naive datetime is
            local time. A time in the past starts the call at once.

    Returns:
        The Job, as run_later() returns it

    Raises:
        TypeError: If when is neither a datetime nor a timedelta
        ValueError: If when is 365 days or more ahead: Step Functions
            stops an execution after a year
        RemoteExecutionError: If the call cannot start
    """
    now = datetime.now(timezone.utc)
    if isinstance(when, timedelta):
        at = now + when
    elif isinstance(when, datetime):
        at = when.astimezone(timezone.utc)
    else:
        raise TypeError(f"when must be a datetime or a timedelta, not {type(when).__name__}")
    if at - now >= LONGEST_DELAY:
        raise ValueError(f"when must be less than {LONGEST_DELAY.days} days ahead, not {at.isoformat()}")
    return _start(fn, args, kwargs, at=at)


def schedule(name: str, expression: str, fn: Reference, /, *args: Any, **kwargs: Any) -> None:
    """
    Run the call of fn on an EventBridge Scheduler expression, in UTC.

    Each run starts as run_later() does. The values of 1 KB or more that
    the call names are copied to schedules/<name>/ in the bucket, which
    the bucket does not expire.

    Args:
        name: 1 to 64 letters, digits, "-", "_" or "."
        expression: rate(...), cron(...) or at(yyyy-mm-ddThh:mm:ss)

    Raises:
        ValueError: If the name is not allowed, or a schedule has it
        SlayError: On the local backend
        StorageError: If SLAY_STORAGE_BUCKET does not name an S3 bucket
        RemoteExecutionError: If Scheduler refuses the schedule, for
            example for a bad expression

    Example:
        >>> slay.schedule("nightly", "cron(0 2 * * ? *)", report, "eu")
    """
    _schedule(name, expression, fn, args, kwargs, replace=False)


def reschedule(name: str, expression: str, fn: Reference, /, *args: Any, **kwargs: Any) -> None:
    """
    Replace the schedule of this name, or create it, as schedule() does.
    The copies that the new call does not name are deleted.

    Raises:
        As schedule(), but a name in use is no error
    """
    _schedule(name, expression, fn, args, kwargs, replace=True)


def unschedule(name: str) -> None:
    """
    Delete the schedule of this name, then its copies.

    Raises:
        ValueError: If no schedule has the name, after any copies under
            that name are deleted
        SlayError: On the local backend
    """
    get_backend().unschedule(name)
    logger.info("unschedule_complete", schedule=name)


def schedules() -> list[str]:
    """
    The names of the schedules, sorted.

    Raises:
        SlayError: On the local backend
    """
    return get_backend().schedule_names()


def _start(fn: Reference, args: tuple, kwargs: dict[str, Any], at: Union[datetime, None]) -> Job:
    backend = get_backend()
    func_ref, retry = without_retry(fn)
    job_id = backend.start_one(func_ref, *_references(args, kwargs), retry, at)
    logger.info("start_one_complete", function_hash=fn.hash[:8], job_id=job_id)
    return Job(job_id, backend)


def _schedule(
    name: str, expression: str, fn: Reference, args: tuple, kwargs: dict[str, Any], replace: bool
) -> None:
    if not isinstance(name, str) or not SCHEDULE_NAME.fullmatch(name):
        raise ValueError(
            f"A schedule name has 1 to 64 letters, digits, '-', '_' or '.', not {name!r}"
        )
    backend = get_backend()
    func_ref, retry = without_retry(fn)
    backend.schedule(name, expression, func_ref, *_references(args, kwargs), retry, replace)
    logger.info("schedule_complete", schedule=name, function_hash=fn.hash[:8])


def _references(args: tuple, kwargs: dict[str, Any]) -> tuple[list[Reference], dict[str, Reference]]:
    """The arguments as run() sends them: a Reference passes through"""
    return [as_reference(arg) for arg in args], {key: as_reference(value) for key, value in kwargs.items()}
