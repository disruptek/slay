"""
Unit tests for trace_operation error logging.
"""

import pytest
import slay
from slay.exceptions import StorageError
from slay.observability.logger import get_logger
from slay.observability.trace_context import trace_operation


@pytest.fixture
def error_messages(monkeypatch):
    """Messages passed to the slay logger's error()"""
    messages = []
    monkeypatch.setattr(
        get_logger(), "error", lambda message, *args, **kwargs: messages.append(message)
    )
    return messages


class TestErrorLogging:
    """Each exception is logged once, by the innermost trace block"""

    def test_nested_failure_is_logged_once(self, error_messages):
        """An exception crossing three blocks gives one entry, from the innermost"""
        with pytest.raises(ValueError):
            with trace_operation("outer"):
                with trace_operation("middle"):
                    with trace_operation("inner"):
                        raise ValueError("boom")

        assert error_messages == ["operation_failed: inner"]

    def test_new_exception_in_outer_block_is_logged(self, error_messages):
        """A wrapper raised by an outer block is a separate failure"""
        with pytest.raises(RuntimeError):
            with trace_operation("outer"):
                try:
                    with trace_operation("inner"):
                        raise ValueError("boom")
                except ValueError as e:
                    raise RuntimeError("wrapped") from e

        assert error_messages == ["operation_failed: inner", "operation_failed: outer"]

    def test_separate_failures_are_each_logged(self, error_messages):
        """Two unrelated failures give two entries"""
        for _ in range(2):
            with pytest.raises(ValueError):
                with trace_operation("op"):
                    raise ValueError("boom")

        assert error_messages == ["operation_failed: op", "operation_failed: op"]

    def test_storage_error_from_put_is_logged_once(self, local_backend, monkeypatch, error_messages):
        """A missing bucket during put() gives one log entry, not one per layer"""
        monkeypatch.delenv("SLAY_STORAGE_BUCKET", raising=False)

        with pytest.raises(StorageError):
            slay.put("x" * 2000)

        assert len(error_messages) == 1
