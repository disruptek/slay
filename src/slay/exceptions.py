"""
Exception classes for slay distributed computing platform.
"""


class SlayError(Exception):
    """Base exception for all slay errors"""

    pass


class RemoteExecutionError(SlayError):
    """Raised when remote execution fails"""

    def __init__(self, message: str, traceback: str | None = None, logs: str | None = None):
        self.message = message
        self.remote_traceback = traceback
        self.remote_logs = logs

        super().__init__(self._format_error())

    def _format_error(self) -> str:
        parts = [f"Remote execution failed: {self.message}"]

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
