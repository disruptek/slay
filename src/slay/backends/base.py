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
from typing import Optional

from slay.core.reference import Reference


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
        self, func_ref: Reference, args: list[Reference], kwargs: dict[str, Reference]
    ) -> Reference:
        """
        Execute function with given arguments.

        The backend resolves every argument reference to its value before
        the call.

        Args:
            func_ref: Reference to function to execute
            args: List of argument references
            kwargs: Keyword argument names mapped to references

        Returns:
            Reference to result

        Raises:
            RemoteExecutionError: If execution fails
        """
        pass

    @abstractmethod
    def execute_many(
        self,
        func_ref: Reference,
        args_list: list[tuple[list[Reference], dict[str, Reference]]],
    ) -> list[Reference]:
        """
        Execute function on multiple argument sets in parallel.

        Args:
            func_ref: Reference to function
            args_list: List of (args_refs, kwargs_refs) tuples, shaped as
                the args and kwargs of execute()

        Returns:
            List of result references in same order as inputs

        Raises:
            RemoteExecutionError: If any execution fails
        """
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
