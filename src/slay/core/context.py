"""
Global execution context for slay.

Manages:
- Backend selection (local, lambda, fargate, etc.)
- Configuration
- Auto-detection of available backends
"""

import os
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from slay.backends.base import ExecutorBackend


class Context:
    """Global execution context"""

    def __init__(self):
        self.backend_name: Optional[str] = None
        self._backend: Optional["ExecutorBackend"] = None
        self._config: dict = {}

    def get_backend(self) -> "ExecutorBackend":
        """
        Get or create execution backend.

        Auto-detects backend if not explicitly set:
        1. Check SLAY_BACKEND env var
        2. Try to connect to Lambda
        3. Fall back to local execution

        Returns:
            ExecutorBackend instance

        Raises:
            ValueError: If backend cannot be created
        """
        if self._backend is not None:
            return self._backend

        # Auto-detect backend
        backend_name = self.backend_name or os.getenv("SLAY_BACKEND", "auto")

        if backend_name == "auto":
            backend_name = self._auto_detect_backend()

        if backend_name == "local":
            from slay.backends.local import LocalBackend

            self._backend = LocalBackend()
        elif backend_name == "lambda":
            from slay.backends.lambda_ import LambdaBackend

            self._backend = LambdaBackend()
        else:
            raise ValueError(f"Unknown backend: {backend_name}")

        return self._backend

    def set_backend(self, backend_name: str) -> None:
        """
        Explicitly set backend.

        Args:
            backend_name: 'local', 'lambda', etc.
        """
        self.backend_name = backend_name
        self._backend = None  # Force recreation

    def set_config(self, key: str, value: any) -> None:
        """
        Set configuration value.

        Args:
            key: Config key
            value: Config value
        """
        self._config[key] = value

    def get_config(self, key: str, default: any = None) -> any:
        """
        Get configuration value.

        Args:
            key: Config key
            default: Default if not set

        Returns:
            Config value or default
        """
        return self._config.get(key, default)

    def _auto_detect_backend(self) -> str:
        """
        Auto-detect which backend to use.

        Returns:
            'lambda' or 'local'
        """
        # Try to use Lambda if AWS credentials available
        try:
            import boto3

            # Test credentials
            sts = boto3.client("sts")
            sts.get_caller_identity()

            # Check if Lambda function exists
            lambda_client = boto3.client("lambda")
            try:
                lambda_client.get_function(FunctionName="slay-executor")
                print("✓ Using AWS Lambda backend")
                return "lambda"
            except lambda_client.exceptions.ResourceNotFoundException:
                print(
                    "⚠️  Lambda function 'slay-executor' not found. "
                    "Run 'cdk deploy' in infrastructure/ to deploy."
                )
                print("⚠️  Falling back to local execution")
                return "local"
        except Exception as e:
            print(f"⚠️  AWS credentials not available ({e})")
            print("⚠️  Using local execution mode")
            return "local"


# Global context instance
_context = Context()


def get_backend() -> "ExecutorBackend":
    """
    Get current execution backend.

    Returns:
        ExecutorBackend instance
    """
    return _context.get_backend()


def set_backend(name: str) -> None:
    """
    Set execution backend.

    Args:
        name: 'local', 'lambda', etc.
    """
    _context.set_backend(name)


def get_context() -> Context:
    """
    Get global context.

    Returns:
        Context instance
    """
    return _context


def set_config(key: str, value: any) -> None:
    """
    Set global configuration.

    Args:
        key: Config key
        value: Config value
    """
    _context.set_config(key, value)


def get_config(key: str, default: any = None) -> any:
    """
    Get global configuration.

    Args:
        key: Config key
        default: Default value

    Returns:
        Config value or default
    """
    return _context.get_config(key, default)
