"""
An in-process stand-in for the boto3 Lambda client.

It runs runtime/handler.py in this process, so every request and response
crosses the JSON boundary as it would on Lambda. LambdaBackend uses it
directly; the Step Functions interpreter in moto uses it for its
lambda:invoke tasks.
"""

import importlib.util
import io
import json
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_handler():
    """Import runtime/handler.py under a private module name"""
    spec = importlib.util.spec_from_file_location(
        "slay_runtime_handler", ROOT / "runtime" / "handler.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def function_error(error_type, message, stack=None):
    """A canned response for a Lambda that failed outside the handler"""
    payload = {"errorType": error_type, "errorMessage": message}
    if stack is not None:
        payload["stackTrace"] = stack
    return {"FunctionError": "Unhandled", "Payload": payload}


class InProcessLambda:
    """
    Stand-in for the boto3 Lambda client that runs the handler in-process.

    Each invocation takes the first entry of queue, if any; otherwise
    canned, if set; otherwise the handler's response. A queued or canned
    response is shaped as the boto3 response with a dict Payload. A queued
    exception is raised instead, as boto3 raises a service error.

    The handler swaps sys.stdout and sys.stderr, so a lock runs one
    invocation at a time. delay(event) gives the seconds to wait after the
    response is ready, which reorders the completions of concurrent calls
    or makes an attempt outlast its timeout.
    """

    def __init__(self):
        self.handler = load_handler()
        self.payloads = []
        self.queue = []
        self.canned = None
        self.delay = lambda event: 0.0
        self._lock = threading.Lock()

    def invoke(self, FunctionName, Payload, InvocationType="RequestResponse", **_):
        event = json.loads(Payload)
        with self._lock:
            self.payloads.append(event)
            if self.queue:
                response = self.queue.pop(0)
                if isinstance(response, Exception):
                    raise response
                response = dict(response)
            elif self.canned is not None:
                response = dict(self.canned)
            else:
                response = {"Payload": self.handler.execute(event, None)}
        time.sleep(self.delay(event))
        body = json.dumps(response["Payload"]).encode("utf-8")
        return {"StatusCode": 200, **response, "Payload": io.BytesIO(body)}
