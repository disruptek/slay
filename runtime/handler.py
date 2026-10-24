"""
AWS Lambda handler for slay function execution.

This code runs inside the Lambda function. It:
1. Receives serialized function and arguments
2. Deserializes them from S3
3. Executes the function through LocalBackend, which also applies wrappers
4. Serializes and stores the result
5. Returns a reference to the result
"""

import sys
import io
import traceback
from typing import Any, Dict, Optional

# The CDK bundle puts the slay package and its dependencies next to this file
from slay.backends.local import LocalBackend
from slay.core.reference import create_reference
from slay.core.wire import decode_call, ref_to_dict

# Characters kept from the end of the output, traceback and message. A
# retried call passes the response through a Step Functions state, which
# holds at most 256 KB.
REPORT_LIMIT = 64 * 1024


def execute(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler for executing serialized functions.

    Args:
        event: Lambda event containing:
            {
                "func_ref": {...},              # Reference to function
                "args_refs": [{...}],           # References to arguments
                "kwargs_refs": {"name": {...}}  # References to keyword arguments
            }
            Each reference is encoded by slay.core.wire.
        context: Lambda context

    Returns:
        {
            "status": "success" | "error",
            "result_ref": {...},       # Reference to result (on success)
            "error": str,              # str(exception) (on error)
            "error_type": str,         # Exception type name (on error)
            "traceback": str,          # Full traceback (on error)
            "exception_ref": {...},    # Reference to the exception (on error)
            "logs": str                # Captured stdout/stderr
        }
        error, traceback and logs keep their last REPORT_LIMIT characters.
        exception_ref is None when the exception cannot be serialized or
        stored. LambdaBackend re-raises the exception it refers to.
    """

    # Capture stdout/stderr for debugging
    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = stdout_capture
    sys.stderr = stderr_capture

    try:
        # 1. Reconstruct references
        func_ref, args_refs, kwargs_refs = decode_call(event)

        # 2-4. Fetch, execute and store the result in this process; a
        # wrapper (for example a cached function) applies here
        result_ref = LocalBackend().execute(func_ref, args_refs, kwargs_refs)

        # 5. Return success
        logs = stdout_capture.getvalue() + stderr_capture.getvalue()
        return {
            "status": "success",
            "result_ref": ref_to_dict(result_ref),
            "logs": _tail(logs),
        }

    except Exception as e:
        # Return error with full context
        logs = stdout_capture.getvalue() + stderr_capture.getvalue()
        return {
            "status": "error",
            "error": _tail(str(e)),
            "error_type": type(e).__name__,
            "traceback": _tail(traceback.format_exc()),
            "exception_ref": _exception_ref(e),
            "logs": _tail(logs),
        }

    finally:
        # Restore stdout/stderr
        sys.stdout = old_stdout
        sys.stderr = old_stderr


def _tail(text: str) -> str:
    """The last REPORT_LIMIT characters of text"""
    return text[-REPORT_LIMIT:]


def _exception_ref(error: Exception) -> Optional[Dict[str, Any]]:
    """The exception as a wire reference, or None if it cannot be stored"""
    try:
        return ref_to_dict(create_reference(error))
    except Exception:
        return None
