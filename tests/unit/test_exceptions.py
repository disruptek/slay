"""
Unit tests for slay's exception classes.
"""

import dill
from slay.exceptions import RemoteExecutionError


class TestRemoteExecutionError:
    """RemoteExecutionError crosses the wire like any other exception"""

    def test_survives_pickling(self):
        """A dill round trip keeps the message text"""
        error = RemoteExecutionError("boom", traceback="remote tb", logs="out")

        copy = dill.loads(dill.dumps(error))

        assert str(copy) == str(error)
        assert copy.message == "boom"

    def test_survives_pickling_with_error_type(self):
        """A dill round trip keeps every attribute"""
        error = RemoteExecutionError(
            "late", traceback="remote tb", logs="out", error_type="Sandbox.Timedout"
        )

        copy = dill.loads(dill.dumps(error))

        assert str(copy) == str(error)
        assert copy.remote_traceback == "remote tb"
        assert copy.remote_logs == "out"
        assert copy.error_type == "Sandbox.Timedout"

    def test_message_names_error_type(self):
        """The text shows the error type before the message"""
        error = RemoteExecutionError("late", error_type="Sandbox.Timedout")

        assert "Sandbox.Timedout: late" in str(error)
