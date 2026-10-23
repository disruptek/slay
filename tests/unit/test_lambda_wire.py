"""
Unit tests for the wire format between LambdaBackend and runtime/handler.py.

No AWS calls: a stand-in for the boto3 Lambda client runs the real handler
in this process, so every request and response crosses the JSON boundary
exactly as it would on Lambda.
"""

import importlib.util
import io
import json
from pathlib import Path

import pytest
import slay
from slay.backends.lambda_ import LambdaBackend
from slay.core.reference import Reference
from slay.core.serializer import TypeInfo

ROOT = Path(__file__).resolve().parents[2]


def _load_handler():
    """Import runtime/handler.py under a private module name"""
    spec = importlib.util.spec_from_file_location(
        "slay_runtime_handler", ROOT / "runtime" / "handler.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InProcessLambda:
    """Stand-in for the boto3 Lambda client that runs the handler in-process"""

    def __init__(self):
        self.handler = _load_handler()
        self.payloads = []

    def invoke(self, FunctionName, InvocationType, Payload):
        event = json.loads(Payload)
        self.payloads.append(event)
        result = self.handler.execute(event, None)
        return {"Payload": io.BytesIO(json.dumps(result).encode("utf-8"))}


@pytest.fixture
def lambda_backend(monkeypatch):
    """LambdaBackend wired to InProcessLambda and used by slay.run()"""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    backend = LambdaBackend()
    backend.lambda_client = InProcessLambda()
    monkeypatch.setattr(slay.api, "get_backend", lambda: backend)
    return backend


def _last_payload(backend):
    return backend.lambda_client.payloads[-1]


class TestLambdaWire:
    """Requests and responses through the Lambda wire format"""

    def test_execute_returns_result(self, lambda_backend):
        """A call through the handler returns a Reference to the result"""
        fn = slay.put(lambda x: x * 2)

        assert slay.run(fn, 21).get() == 42

    def test_keyword_arguments_are_sent_as_references(self, lambda_backend):
        """Each keyword argument travels as its own reference"""
        fn = slay.put(lambda x, factor: x * factor)

        assert slay.run(fn, 2, factor=3).get() == 6
        assert set(_last_payload(lambda_backend)["kwargs_refs"]) == {"factor"}

    def test_reference_argument_passes_through(self, lambda_backend):
        """A Reference argument is sent as that reference, not re-serialized"""
        six = slay.put(6)
        three = slay.put(3)
        fn = slay.put(lambda x, d: x // d)

        assert slay.run(fn, six, d=three).get() == 2
        payload = _last_payload(lambda_backend)
        assert payload["args_refs"][0]["hash"] == six.hash
        assert payload["kwargs_refs"]["d"]["hash"] == three.hash

    def test_chained_results(self, lambda_backend):
        """A result Reference from one call feeds the next call"""
        double = slay.put(lambda x: x * 2)

        assert double(double(3)).get() == 12

    def test_apply(self, lambda_backend):
        """apply() runs every item through the handler"""
        fn = slay.put(lambda x: x + 1)

        assert slay.get_many(slay.apply(fn, [1, slay.put(2), 3])) == [2, 3, 4]

    def test_large_values_use_storage(self, lambda_backend, mock_s3_bucket):
        """Values of 1 KB or more cross the wire as storage keys"""
        fn = slay.put(lambda s: s.upper())
        big = "x" * 2000

        assert fn(big).get() == big.upper()
        assert _last_payload(lambda_backend)["args_refs"][0]["storage_tier"] == "s3"

    def test_remote_error_carries_traceback(self, lambda_backend):
        """A remote exception comes back with its traceback"""

        def boom(x):
            raise ValueError("boom")

        with pytest.raises(slay.RemoteExecutionError) as excinfo:
            slay.put(boom)(1)

        assert "ValueError" in excinfo.value.remote_traceback

    def test_python_version_mismatch_reaches_client(self, lambda_backend):
        """A function from another Python minor version fails with a clear message"""
        real = slay.put(lambda x: x)
        foreign = Reference(
            hash=real.hash,
            size=real.size,
            storage_tier=real.storage_tier,
            type_info=TypeInfo(
                format="dill",
                type_name="function",
                module="__main__",
                metadata={"python": "3.0"},
            ),
            inline_data=real.inline_data,
        )

        with pytest.raises(slay.RemoteExecutionError, match="Python 3.0"):
            foreign(1)
