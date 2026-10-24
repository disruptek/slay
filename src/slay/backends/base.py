"""
Abstract base class for execution backends.

Backends handle running functions in different environments:
- Local (testing)
- AWS Lambda
- ECS Fargate
- EC2
- Custom environments
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from slay.core.reference import Reference

if TYPE_CHECKING:
    from slay.wrappers import Retried


class ExecutorBackend(ABC):
    """
    Abstract backend for executing functions.

    A backend is responsible for:
    1. Taking a function reference and arguments
    2. Deserializing them from storage
    3. Executing the function
    4. Serializing and storing the result
    5. Returning a reference to the result
    """

    @abstractmethod
    def execute(
        self,
        func_ref: Reference,
        args: list[Reference],
        kwargs: dict[str, Reference],
        retry: Optional["Retried"] = None,
    ) -> Reference:
        """
        Execute function with given arguments.

        The backend resolves every argument reference to its value before
        the call.

        Args:
            func_ref: Reference to function to execute
            args: List of argument references
            kwargs: Keyword argument names mapped to references
            retry: A slay.wrappers.Retried policy, or None for one attempt

        Returns:
            Reference to result

        Raises:
            Exception: The exception the function raised, of its own type.
                A remote backend sets its __cause__ to a RemoteExecutionError
                with the remote traceback and the captured output.
            RemoteExecutionError: If the backend fails outside the function,
                or if the function's exception cannot be carried back
        """
        pass

    @abstractmethod
    def execute_many(
        self,
        func_ref: Reference,
        args_list: list[tuple[list[Reference], dict[str, Reference]]],
        retry: Optional["Retried"] = None,
        max_concurrent: Optional[int] = None,
    ) -> list[Reference]:
        """
        Execute function on multiple argument sets in parallel.

        Args:
            func_ref: Reference to function
            args_list: List of (args_refs, kwargs_refs) tuples, shaped as
                the args and kwargs of execute()
            retry: A Retried policy for every call, or None
            max_concurrent: The most calls at once, or None for the
                backend's own limit

        Returns:
            List of result references in same order as inputs

        Raises:
            Exception: The error of the lowest failing index, as execute()
                raises it
        """
        pass

    @abstractmethod
    def start_many(
        self,
        func_ref: Reference,
        args_list: list[tuple[list[Reference], dict[str, Reference]]],
        retry: Optional["Retried"] = None,
        max_concurrent: Optional[int] = None,
    ) -> str:
        """
        Start execute_many as a job and return its ID; job_results gives
        the outcome, so a client can leave and come back.
        """
        pass

    @abstractmethod
    def job_status(self, job_id: str) -> str:
        """
        The job's status: "RUNNING", "SUCCEEDED", "FAILED", "TIMED_OUT" or
        "ABORTED". A job whose calls raised has finished: "SUCCEEDED".
        """
        pass

    @abstractmethod
    def job_results(self, job_id: str) -> list[Reference]:
        """
        Wait for the job, then return its results or raise as execute_many.
        A job of one call (start_one) raises ValueError.
        """
        pass

    @abstractmethod
    def start_one(
        self,
        func_ref: Reference,
        args: list[Reference],
        kwargs: dict[str, Reference],
        retry: Optional["Retried"] = None,
        at: Optional[datetime] = None,
    ) -> str:
        """
        Start execute as a job of one call and return its ID. With at, an
        aware datetime, the call starts no earlier than that time.
        """
        pass

    @abstractmethod
    def job_result(self, job_id: str) -> Reference:
        """
        Wait for a job of one call, then return its result or raise as
        execute. A job of many calls (start_many) raises ValueError.
        """
        pass

    @abstractmethod
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
        """
        Run the call on an EventBridge Scheduler expression, in UTC, as a
        job of one call each time. Without replace, a name in use raises
        ValueError; with it, the schedule of that name is replaced or
        created.
        """
        pass

    @abstractmethod
    def unschedule(self, name: str) -> None:
        """Delete the schedule; ValueError if no schedule has the name"""
        pass

    @abstractmethod
    def schedule_names(self) -> list[str]:
        """The names of the schedules, sorted"""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """
        Backend identifier.

        Returns:
            Short name like 'local', 'lambda', etc.
        """
        pass

    @property
    @abstractmethod
    def max_execution_time(self) -> Optional[int]:
        """
        Maximum execution time in seconds.

        Returns:
            Max seconds, or None for unlimited
        """
        pass
