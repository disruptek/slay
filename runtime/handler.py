"""
AWS Lambda handler for slay function execution.

This code runs inside the Lambda function. It:
1. Receives serialized function and arguments
2. Deserializes them from S3
3. Executes the function
4. Serializes and stores the result
5. Returns a reference to the result
"""

import sys
import io
import traceback
from typing import Any, Dict

# The CDK bundle puts the slay package and its dependencies next to this file
from slay.core.reference import create_reference
from slay.core.storage import resolve_reference
from slay.core.wire import decode_call, ref_to_dict


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
            "error": str,              # Error message (on error)
            "traceback": str,          # Full traceback (on error)
            "logs": str                # Captured stdout/stderr
        }
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

        # 2. Fetch and deserialize
        func = resolve_reference(func_ref)
        args = [resolve_reference(ref) for ref in args_refs]
        kwargs = {name: resolve_reference(ref) for name, ref in kwargs_refs.items()}

        # 3. Execute
        result = func(*args, **kwargs)

        # 4. Store result
        result_ref = create_reference(result)

        # 5. Return success
        logs = stdout_capture.getvalue() + stderr_capture.getvalue()
        return {
            "status": "success",
            "result_ref": ref_to_dict(result_ref),
            "logs": logs,
        }

    except Exception as e:
        # Return error with full context
        logs = stdout_capture.getvalue() + stderr_capture.getvalue()
        return {
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc(),
            "logs": logs,
        }

    finally:
        # Restore stdout/stderr
        sys.stdout = old_stdout
        sys.stderr = old_stderr
