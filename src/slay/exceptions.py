"""
Exception classes for slay distributed computing platform.
"""


class SlayError(Exception):
    """Base exception for all slay errors"""

    pass


class RemoteExecutionError(SlayError):
    """
    What the backend reports about a failed remote call.

    When the function raises on Lambda, the caller gets the function's own
    exception, and this error is its __cause__. The caller gets this error
    itself when the exception cannot cross the wire, when the Lambda fails
    outside the function (timeout, out of memory, runtime crash), or when
    the invocation fails.

    Attributes:
        message: The error message (str(exception), or the Lambda
            errorMessage)
        remote_traceback: The traceback text from the backend, or None
        remote_logs: The stdout and stderr captured during the call, or None
        error_type: The remote exception's type name, or the Lambda
            errorType (for example "Sandbox.Timedout"), or None

    The error survives pickling with its message and attributes, so a
    function may raise it on one backend and the caller still reads it.
    """

    def __init__(
        self,
        message: str,
        traceback: str | None = None,
        logs: str | None = None,
        error_type: str | None = None,
    ):
        self.message = message
        self.remote_traceback = traceback
        self.remote_logs = logs
        self.error_type = error_type

        super().__init__(self._format_error())

    def __reduce__(self):
        # The default rebuilds from self.args, the formatted text, which
        # would format the message a second time
        fields = (self.message, self.remote_traceback, self.remote_logs, self.error_type)
        return (type(self), fields, self.__dict__)

    def _format_error(self) -> str:
        summary = f"{self.error_type}: {self.message}" if self.error_type else self.message
        parts = [f"Remote execution failed: {summary}"]

        if self.remote_traceback:
            parts.append("\nRemote traceback:")
            parts.append(self.remote_traceback)

        if self.remote_logs:
            parts.append("\nRemote logs:")
            parts.append(self.remote_logs)

        return "\n".join(parts)


class StorageError(SlayError):
    """Raised when storage operations fail"""

    pass


class SerializationError(SlayError):
    """Raised when serialization/deserialization fails"""

    pass


class CacheMiss(SlayError):
    """Raised by a function from add_cache_only when no cache entry exists"""

    pass
