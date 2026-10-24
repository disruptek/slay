"""
Unit tests for the wire format between LambdaBackend and runtime/handler.py.

No AWS calls: a stand-in for the boto3 Lambda client runs the real handler
in this process, so every request and response crosses the JSON boundary
exactly as it would on Lambda.
"""

import pytest
import slay
from lambda_stand_in import InProcessLambda
from slay.backends.lambda_ import LambdaBackend
from slay.core.reference import Reference
from slay.core.serializer import TypeInfo
from slay.core.wire import ref_to_dict
from slay.exceptions import SerializationError, StorageError

REPORT_LIMIT = 64 * 1024  # characters of output and traceback the handler keeps


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


class CodedError(Exception):
    """An exception whose __init__ takes its args, with a custom message"""

    def __init__(self, code):
        super().__init__(code)
        self.code = code

    def __str__(self):
        return f"code {self.code}"


class TwoPartError(Exception):
    """An exception whose __init__ parameters differ from its args"""

    def __init__(self, left, right):
        super().__init__(f"{left}/{right}")


class GeneratorError(Exception):
    """An exception that dill cannot serialize: it holds a generator"""

    def __init__(self):
        super().__init__("holds a generator")
        self.pending = (n for n in range(3))


def _refuse_to_load():
    raise RuntimeError("this exception cannot be loaded")


class UnloadableError(Exception):
    """An exception that serializes, but whose loading raises"""

    def __reduce__(self):
        return (_refuse_to_load, ())


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

    def test_large_values_use_storage(self, lambda_backend, mock_s3_bucket):
        """Values of 1 KB or more cross the wire as storage keys"""
        fn = slay.put(lambda s: s.upper())
        big = "x" * 2000

        assert fn(big).get() == big.upper()
        assert _last_payload(lambda_backend)["args_refs"][0]["storage_tier"] == "s3"

    def test_remote_error_carries_traceback(self, lambda_backend):
        """A remote exception keeps its type; its cause holds the traceback"""

        def boom(x):
            raise ValueError("boom")

        with pytest.raises(ValueError) as excinfo:
            slay.put(boom)(1)

        assert type(excinfo.value) is ValueError
        assert str(excinfo.value) == "boom"
        cause = excinfo.value.__cause__
        assert isinstance(cause, slay.RemoteExecutionError)
        assert "ValueError: boom" in cause.remote_traceback
        assert cause.error_type == "ValueError"
        assert cause.message == "boom"

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

        with pytest.raises(SerializationError, match="Python 3.0") as excinfo:
            foreign(1)

        assert isinstance(excinfo.value.__cause__, slay.RemoteExecutionError)


class TestLambdaErrors:
    """How failures on Lambda reach the caller"""

    def test_cause_carries_captured_output(self, lambda_backend):
        """The cause holds what the function printed before it raised"""

        def noisy(x):
            print("partial output")
            raise ValueError("noisy")

        with pytest.raises(ValueError) as excinfo:
            slay.put(noisy)(1)

        assert "partial output" in excinfo.value.__cause__.remote_logs

    def test_exception_keeps_attributes(self, lambda_backend):
        """A custom exception arrives with its class and its attributes"""

        def coded(x):
            error = CodedError(x)
            error.detail = "set after construction"
            raise error

        with pytest.raises(CodedError) as excinfo:
            slay.put(coded)(7)

        assert excinfo.value.code == 7
        assert excinfo.value.detail == "set after construction"
        assert str(excinfo.value) == "code 7"

    def test_exception_with_mismatched_init_falls_back(self, lambda_backend):
        """An exception that cannot be rebuilt from args gives RemoteExecutionError"""

        def split(x):
            raise TwoPartError("left", "right")

        with pytest.raises(slay.RemoteExecutionError) as excinfo:
            slay.put(split)(1)

        assert excinfo.value.error_type == "TwoPartError"
        assert excinfo.value.message == "left/right"

    def test_slay_error_in_handler_keeps_type(self, lambda_backend, monkeypatch):
        """A slay error raised in the handler reaches the caller as itself"""
        monkeypatch.delenv("SLAY_STORAGE_BUCKET", raising=False)
        fn = slay.put(lambda n: "x" * n)

        with pytest.raises(StorageError, match="SLAY_STORAGE_BUCKET") as excinfo:
            fn(2000)

        assert isinstance(excinfo.value.__cause__, slay.RemoteExecutionError)

    def test_nested_remote_error_crosses_the_wire(self, lambda_backend):
        """A RemoteExecutionError raised by the function arrives intact"""

        # Names the slay module, which dill stores by name
        def relay(x):
            raise slay.RemoteExecutionError(
                "inner failure", traceback="inner tb", error_type="Sandbox.Timedout"
            )

        with pytest.raises(slay.RemoteExecutionError) as excinfo:
            slay.put(relay)(1)

        error = excinfo.value
        assert error.message == "inner failure"
        assert error.remote_traceback == "inner tb"
        assert error.error_type == "Sandbox.Timedout"
        assert isinstance(error.__cause__, slay.RemoteExecutionError)
        assert error.__cause__.error_type == "RemoteExecutionError"

    def test_unserializable_exception_falls_back(self, lambda_backend):
        """An exception dill cannot serialize arrives as RemoteExecutionError"""

        def hold(x):
            raise GeneratorError()

        with pytest.raises(slay.RemoteExecutionError) as excinfo:
            slay.put(hold)(1)

        assert excinfo.value.error_type == "GeneratorError"
        assert excinfo.value.message == "holds a generator"
        assert "GeneratorError" in excinfo.value.remote_traceback

    def test_unloadable_exception_falls_back(self, lambda_backend):
        """An exception the client cannot load arrives as RemoteExecutionError"""

        def refuse(x):
            raise UnloadableError("unloadable")

        with pytest.raises(slay.RemoteExecutionError) as excinfo:
            slay.put(refuse)(1)

        assert excinfo.value.error_type == "UnloadableError"
        assert "UnloadableError" in excinfo.value.remote_traceback

    def test_response_without_exception_falls_back(self, lambda_backend):
        """An error response with no exception_ref gives RemoteExecutionError"""
        lambda_backend.lambda_client.canned = {
            "Payload": {
                "status": "error",
                "error": "boom",
                "error_type": "ValueError",
                "traceback": "remote tb",
                "logs": "remote out",
            }
        }

        with pytest.raises(slay.RemoteExecutionError) as excinfo:
            slay.put(lambda x: x)(1)

        error = excinfo.value
        assert (error.message, error.error_type) == ("boom", "ValueError")
        assert (error.remote_traceback, error.remote_logs) == ("remote tb", "remote out")

    def test_exception_ref_to_a_non_exception_falls_back(self, lambda_backend):
        """An exception_ref that holds no exception gives RemoteExecutionError"""
        lambda_backend.lambda_client.canned = {
            "Payload": {
                "status": "error",
                "error": "boom",
                "error_type": "ValueError",
                "traceback": "remote tb",
                "exception_ref": ref_to_dict(slay.put(5)),
                "logs": "",
            }
        }

        with pytest.raises(slay.RemoteExecutionError) as excinfo:
            slay.put(lambda x: x)(1)

        assert excinfo.value.error_type == "ValueError"

    def test_function_error_reports_lambda_error(self, lambda_backend):
        """A Lambda timeout reports errorType and errorMessage"""
        message = "2026-10-23T00:00:00.000Z 1234 Task timed out after 840.00 seconds"
        lambda_backend.lambda_client.canned = {
            "FunctionError": "Unhandled",
            "Payload": {"errorType": "Sandbox.Timedout", "errorMessage": message},
        }

        with pytest.raises(slay.RemoteExecutionError) as excinfo:
            slay.put(lambda x: x)(1)

        error = excinfo.value
        assert error.error_type == "Sandbox.Timedout"
        assert error.message == message
        assert error.remote_traceback is None
        assert "result_ref" not in str(error)

    def test_function_error_keeps_stack_trace(self, lambda_backend):
        """A Python error outside the handler reports its stack trace"""
        stack = [
            '  File "/var/task/handler.py", line 18, in <module>\n',
            "    from slay.core.reference import create_reference\n",
        ]
        lambda_backend.lambda_client.canned = {
            "FunctionError": "Unhandled",
            "Payload": {
                "errorType": "Runtime.ImportModuleError",
                "errorMessage": "Unable to import module 'handler': No module named 'dill'",
                "stackTrace": stack,
            },
        }

        with pytest.raises(slay.RemoteExecutionError) as excinfo:
            slay.put(lambda x: x)(1)

        assert excinfo.value.error_type == "Runtime.ImportModuleError"
        assert excinfo.value.remote_traceback == "".join(stack)


class TestLambdaWrappers:
    """Wrappers over the Lambda backend"""

    def test_cached_call_sends_inner_function(self, lambda_backend, mock_s3_bucket):
        """The client looks up the cache and sends the inner function on a miss"""
        fn = slay.put(lambda x: x * 2)
        cached = slay.add_caching(fn)

        assert cached(21).get() == 42
        assert _last_payload(lambda_backend)["func_ref"]["hash"] == fn.hash

    def test_cache_hit_makes_no_invocation(self, lambda_backend, mock_s3_bucket):
        """A hit is answered by the client alone"""
        cached = slay.add_caching(slay.put(lambda x: x * 2))
        cached(21)
        sent = len(lambda_backend.lambda_client.payloads)

        assert cached(21).get() == 42
        assert len(lambda_backend.lambda_client.payloads) == sent

    def test_handler_applies_a_wrapper(self, lambda_backend, mock_s3_bucket):
        """A wrapper sent to the handler runs its policy in the executor"""
        cached = slay.add_caching(slay.put(lambda x: x * 2))

        result = lambda_backend.execute(cached, [slay.put(21)], {})
        assert result.get() == 42
        assert _last_payload(lambda_backend)["func_ref"]["hash"] == cached.hash

        sent = len(lambda_backend.lambda_client.payloads)
        assert cached(21).get() == 42
        assert len(lambda_backend.lambda_client.payloads) == sent


class TestHandlerReportSize:
    """The handler keeps the end of long reports, so a response fits a
    Step Functions state (256 KB)"""

    def test_logs_keep_the_last_64_kb(self, lambda_backend):
        """Long output is cut from the front"""

        def chatty(x):
            print("a" * 100_000)
            print("the end")
            raise ValueError("chatty")

        with pytest.raises(ValueError) as excinfo:
            slay.put(chatty)(1)

        logs = excinfo.value.__cause__.remote_logs
        assert len(logs) <= REPORT_LIMIT
        assert logs.endswith("the end\n")

    def test_traceback_and_message_keep_the_last_64_kb(self, lambda_backend, mock_s3_bucket):
        """A long message is cut from the front in the traceback and the error.
        The exception pickles to more than 1 KB, so it needs the bucket."""

        def shout(x):
            raise ValueError("b" * 100_000 + " the end")

        with pytest.raises(ValueError) as excinfo:
            slay.put(shout)(1)

        cause = excinfo.value.__cause__
        assert len(cause.remote_traceback) <= REPORT_LIMIT
        assert cause.remote_traceback.rstrip().endswith("the end")
        assert len(cause.message) <= REPORT_LIMIT
        assert cause.message.endswith("the end")
