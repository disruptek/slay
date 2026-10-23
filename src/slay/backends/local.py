"""
Local execution backend for development and testing.

Executes functions in the current process (useful for:
- Development/debugging
- Testing without AWS
- Running in environments without AWS access
"""

from typing import Optional

from slay.backends.base import ExecutorBackend
from slay.core.reference import Reference, create_reference
from slay.exceptions import RemoteExecutionError


class LocalBackend(ExecutorBackend):
    """Execute functions locally in current process"""

    @property
    def name(self) -> str:
        return "local"

    @property
    def max_execution_time(self) -> Optional[int]:
        return None  # Unlimited

    def execute(
        self, func_ref: Reference, args: list[Reference], kwargs: dict[str, Reference]
    ) -> Reference:
        """
        Execute function locally.

        Args:
            func_ref: Reference to function
            args: List of argument references
            kwargs: Keyword argument names mapped to references

        Returns:
            Reference to result

        Raises:
            RemoteExecutionError: If execution fails
        """
        try:
            # Deserialize
            func = func_ref.data
            args_values = [arg.data for arg in args]
            kwargs_values = {name: ref.data for name, ref in kwargs.items()}

            # Execute
            result = func(*args_values, **kwargs_values)

            # Return as reference
            return create_reference(result)
        except Exception as e:
            raise RemoteExecutionError(
                message=str(e),
                traceback=None,
                logs=None,
            ) from e

    def execute_many(
        self,
        func_ref: Reference,
        args_list: list[tuple[list[Reference], dict[str, Reference]]],
    ) -> list[Reference]:
        """
        Execute function on multiple inputs serially.

        Note: LocalBackend doesn't use parallelism. For actual parallel
        execution, deploy with Lambda or Fargate backend.

        Args:
            func_ref: Reference to function
            args_list: List of (args_refs, kwargs_refs) tuples

        Returns:
            List of result references in same order
        """
        results = []
        for i, (args, kwargs) in enumerate(args_list):
            try:
                result_ref = self.execute(func_ref, args, kwargs)
                results.append(result_ref)
            except Exception as e:
                raise RemoteExecutionError(
                    message=f"Task {i} failed: {e}",
                    traceback=None,
                    logs=None,
                ) from e
        return results
