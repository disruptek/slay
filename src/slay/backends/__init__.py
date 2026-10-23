"""Backend implementations for slay"""

from slay.backends.base import ExecutorBackend
from slay.backends.local import LocalBackend

__all__ = ["ExecutorBackend", "LocalBackend"]
