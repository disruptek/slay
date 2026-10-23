"""
AWS Lambda execution backend.

Executes functions on AWS Lambda for scalable, serverless compute.
Supports unlimited parallelism up to AWS account limits.
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import boto3

from slay.backends.base import ExecutorBackend
from slay.core.reference import Reference
from slay.core.wire import dict_to_ref, encode_call
from slay.exceptions import RemoteExecutionError


class LambdaBackend(ExecutorBackend):
    """Execute functions on AWS Lambda"""

    def __init__(self):
        self.lambda_client = boto3.client("lambda")
        self.function_name = "slay-executor"

    @property
    def name(self) -> str:
        return "lambda"

    @property
    def max_execution_time(self) -> Optional[int]:
        return 900  # 15 minutes

    def execute(
        self, func_ref: Reference, args: list[Reference], kwargs: dict[str, Reference]
    ) -> Reference:
        """
        Execute on AWS Lambda.

        Args:
            func_ref: Reference to function
            args: List of argument references
            kwargs: Keyword argument names mapped to references

        Returns:
            Reference to result

        Raises:
            RemoteExecutionError: If execution fails
        """
        payload = encode_call(func_ref, args, kwargs)

        # Invoke Lambda
        try:
            response = self.lambda_client.invoke(
                FunctionName=self.function_name,
                InvocationType="RequestResponse",
                Payload=json.dumps(payload),
            )
        except Exception as e:
            raise RemoteExecutionError(
                message=f"Failed to invoke Lambda: {e}",
                traceback=None,
                logs=None,
            ) from e

        # Parse response
        try:
            result_data = json.loads(response["Payload"].read())
        except Exception as e:
            raise RemoteExecutionError(
                message=f"Failed to parse Lambda response: {e}",
                traceback=None,
                logs=None,
            ) from e

        # Check for errors
        if result_data.get("status") == "error":
            raise RemoteExecutionError(
                message=result_data.get("error", "Unknown error"),
                traceback=result_data.get("traceback"),
                logs=result_data.get("logs"),
            )

        # Reconstruct result reference
        try:
            return dict_to_ref(result_data["result_ref"])
        except Exception as e:
            raise RemoteExecutionError(
                message=f"Failed to reconstruct result: {e}",
                traceback=None,
                logs=result_data.get("logs"),
            ) from e

    def execute_many(
        self,
        func_ref: Reference,
        args_list: list[tuple[list[Reference], dict[str, Reference]]],
    ) -> list[Reference]:
        """
        Execute many in parallel using ThreadPoolExecutor.

        Lambdas can handle thousands of concurrent invocations. We use
        ThreadPoolExecutor to parallelize the invocation calls.

        Args:
            func_ref: Reference to function
            args_list: List of (args_refs, kwargs_refs) tuples

        Returns:
            List of result references in same order

        Raises:
            RemoteExecutionError: If any execution fails (fail-fast)
        """
        if not args_list:
            return []

        # Lambda can handle thousands of concurrent invocations
        # Cap at 500 threads to avoid resource exhaustion
        max_workers = min(len(args_list), 500)

        results = [None] * len(args_list)
        errors = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_idx = {
                executor.submit(self.execute, func_ref, args, kwargs): idx
                for idx, (args, kwargs) in enumerate(args_list)
            }

            # Collect results as they complete
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    # Store error but continue collecting results
                    errors.append((idx, e))

        # If any errors, raise first one
        if errors:
            idx, error = errors[0]
            raise RemoteExecutionError(
                message=f"Task {idx} failed: {error}",
                traceback=None,
                logs=None,
            ) from error

        return results
