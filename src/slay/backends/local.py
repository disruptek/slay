"""
Local execution backend for development and testing.

Executes functions in the current process (useful for:
- Development/debugging
- Testing without AWS
- Running in environments without AWS access
"""

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Optional, Union

from slay.backends.base import ExecutorBackend
from slay.core.reference import Reference, create_reference
from slay.exceptions import SlayError
from slay.wrappers import call, call_many, is_wrapper

if TYPE_CHECKING:
    from slay.wrappers import Retried


@dataclass(frozen=True)
class _Job:
    """
    A job of this process.

    Attributes:
        many: True for a job of start_many, False for one of start_one
        outcome: The results, the result, or the error that ended the job
    """

    many: bool
    outcome: Union[list[Reference], Reference, Exception]


# The jobs of this process by ID
_jobs: dict[str, _Job] = {}


class LocalBackend(ExecutorBackend):
    """Execute functions locally in current process"""

    @property
    def name(self) -> str:
        return "local"

    @property
    def max_execution_time(self) -> Optional[int]:
        return None  # Unlimited

    def execute(
        self,
        func_ref: Reference,
        args: list[Reference],
        kwargs: dict[str, Reference],
        retry: Optional["Retried"] = None,
    ) -> Reference:
        """
        Execute function locally.

        With retry, a failed attempt runs again after retry.waits() seconds,
        if retry.retry_on_failure. An attempt cannot be interrupted, so
        retry.timeout does not apply and no attempt times out.

        Args:
            func_ref: Reference to function
            args: List of argument references
            kwargs: Keyword argument names mapped to references
            retry: A Retried policy, or None for one attempt

        Returns:
            Reference to result

        Raises:
            Exception: The exception the function raised (the last
                attempt's, with retry), unchanged, with its own traceback
        """
        if retry is None:
            return self._run(func_ref, args, kwargs)

        for wait in retry.waits():
            try:
                return self._run(func_ref, args, kwargs)
            except Exception:
                if not retry.retry_on_failure:
                    raise
            time.sleep(wait)
        return self._run(func_ref, args, kwargs)

    def _run(
        self, func_ref: Reference, args: list[Reference], kwargs: dict[str, Reference]
    ) -> Reference:
        """One attempt in this process"""
        # A wrapper applies here, with this backend for its inner calls
        if is_wrapper(func_ref):
            return call(func_ref, args, kwargs, self)

        # Deserialize
        func = func_ref.data
        args_values = [arg.data for arg in args]
        kwargs_values = {name: ref.data for name, ref in kwargs.items()}

        # Execute
        result = func(*args_values, **kwargs_values)

        # Return as reference
        return create_reference(result)

    def execute_many(
        self,
        func_ref: Reference,
        args_list: list[tuple[list[Reference], dict[str, Reference]]],
        retry: Optional["Retried"] = None,
        max_concurrent: Optional[int] = None,
    ) -> list[Reference]:
        """
        Execute function on multiple inputs serially.

        Note: LocalBackend doesn't use parallelism. For actual parallel
        execution, deploy with Lambda or Fargate backend.

        Args:
            func_ref: Reference to function
            args_list: List of (args_refs, kwargs_refs) tuples
            retry: A Retried policy for every call, as in execute()
            max_concurrent: Ignored; one call runs at a time

        Returns:
            List of result references in same order

        Raises:
            Exception: The first error, unchanged. Items after it do not run.
        """
        if retry is None and is_wrapper(func_ref):
            return call_many(func_ref, args_list, self, max_concurrent)
        return [self.execute(func_ref, args, kwargs, retry) for args, kwargs in args_list]

    def start_many(
        self,
        func_ref: Reference,
        args_list: list[tuple[list[Reference], dict[str, Reference]]],
        retry: Optional["Retried"] = None,
        max_concurrent: Optional[int] = None,
    ) -> str:
        """
        Run execute_many now and keep its results, or its error, in the
        memory of this process under a new job ID.
        """
        return _record(True, lambda: self.execute_many(func_ref, args_list, retry, max_concurrent))

    def start_one(
        self,
        func_ref: Reference,
        args: list[Reference],
        kwargs: dict[str, Reference],
        retry: Optional["Retried"] = None,
        at: Optional[datetime] = None,
    ) -> str:
        """
        Sleep until at, if it is in the future, then run execute and keep
        its result, or its error, in the memory of this process under a
        new job ID.
        """
        if at is not None:
            delay = (at - datetime.now(timezone.utc)).total_seconds()
            if delay > 0:
                time.sleep(delay)
        return _record(False, lambda: self.execute(func_ref, args, kwargs, retry))

    def job_status(self, job_id: str) -> str:
        """
        "SUCCEEDED": start_many finished the job.

        Raises:
            ValueError: If this process has no job with this ID
        """
        _job(job_id)
        return "SUCCEEDED"

    def job_results(self, job_id: str) -> list[Reference]:
        """
        The job's results, or its error raised again.

        Raises:
            ValueError: If this process has no job of many calls with this ID
        """
        return list(_outcome(job_id, many=True))

    def job_result(self, job_id: str) -> Reference:
        """
        The job's result, or its error raised again.

        Raises:
            ValueError: If this process has no job of one call with this ID
        """
        return _outcome(job_id, many=False)

    def schedule(
        self,
        name: str,
        expression: str,
        func_ref: Reference,
        args: list[Reference],
        kwargs: dict[str, Reference],
        retry: Optional["Retried"] = None,
        replace: bool = False,
    ) -> None:
        """Raises SlayError: no process outlives a schedule"""
        raise _no_schedules()

    def unschedule(self, name: str) -> None:
        """Raises SlayError: no process outlives a schedule"""
        raise _no_schedules()

    def schedule_names(self) -> list[str]:
        """Raises SlayError: no process outlives a schedule"""
        raise _no_schedules()


def _record(many: bool, run: Callable[[], Union[list[Reference], Reference]]) -> str:
    """Run a job now and keep its outcome, or its error, under a new ID"""
    job_id = uuid.uuid4().hex
    try:
        outcome = run()
    except Exception as error:
        outcome = error
    _jobs[job_id] = _Job(many=many, outcome=outcome)
    return job_id


def _job(job_id: str) -> _Job:
    """A job of this process"""
    try:
        return _jobs[job_id]
    except KeyError:
        raise ValueError(f"No job {job_id} in this process") from None


def _outcome(job_id: str, many: bool) -> Union[list[Reference], Reference]:
    """
    The outcome of a job of this kind, or its error raised again.

    Raises:
        ValueError: If this process has no job with this ID, or the job is
            of the other kind
    """
    job = _job(job_id)
    if job.many != many:
        held, wanted = ("many calls", "results()") if job.many else ("one call", "result()")
        raise ValueError(f"Job {job_id} holds {held}; use {wanted}")
    if isinstance(job.outcome, Exception):
        raise job.outcome
    return job.outcome


def _no_schedules() -> SlayError:
    return SlayError(
        "The local backend cannot keep schedules: no process outlives them. "
        "Use the Lambda backend."
    )
