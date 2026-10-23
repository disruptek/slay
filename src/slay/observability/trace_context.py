"""
X-Ray context manager for Slay operations (OPTIONAL).

Provides:
- Unified @contextmanager for any operation
- Automatic subsegment creation
- Privacy-safe annotations
- Error capture
- Log correlation via trace_id
- Timing metrics

Usage:
    with trace_operation("put", object_type="function"):
        ref = create_reference(fn)
"""

import time
from contextlib import contextmanager
from typing import Optional, Generator

from slay.observability.logger import get_logger
from slay.observability.xray import get_xray_recorder, is_xray_enabled

# Attribute set on an exception once a trace block has logged it
_LOGGED_MARK = "_slay_trace_logged"


@contextmanager
def trace_operation(operation_name: str, **annotations) -> Generator[None, None, None]:
    """
    Context manager for tracing any Slay operation.
    
    - Creates X-Ray subsegment (if enabled)
    - Adds privacy-safe annotations
    - Captures errors automatically; an exception that leaves several
      nested blocks is logged once, by the innermost block
    - Injects trace_id into logs
    - Records timing metadata
    
    Args:
        operation_name: Name of operation (e.g., "put", "run", "storage.s3_get")
        **annotations: Privacy-safe annotations (e.g., status="success", tier="s3")
    
    Usage:
        with trace_operation("put", object_type="function"):
            ref = create_reference(fn)
        
        with trace_operation("lambda_invoke", backend="lambda"):
            response = lambda_client.invoke(...)
    """
    logger = get_logger()
    xray = get_xray_recorder()
    
    # Start X-Ray subsegment if enabled
    if is_xray_enabled():
        xray.begin_subsegment(operation_name)
    
    # Add annotations (privacy-safe)
    for key, value in annotations.items():
        if is_xray_enabled():
            xray.put_annotation(key, value)
    
    # Set trace_id in logger context for CloudWatch correlation
    trace_id = xray.get_trace_id()
    if trace_id:
        logger.set_trace_id(trace_id)
    
    start_time = time.time()
    
    try:
        # Execute wrapped code
        yield
        
        # Mark success in X-Ray
        if is_xray_enabled():
            xray.put_annotation('status', 'success')
        
    except Exception as e:
        # Capture error in X-Ray
        if is_xray_enabled():
            xray.capture_error(e)
            xray.put_annotation('status', 'error')
            xray.put_annotation('error_type', type(e).__name__)
        
        # Log error, once: the innermost block sees the exception first
        if not getattr(e, _LOGGED_MARK, False):
            logger.error(
                f'operation_failed: {operation_name}',
                error_type=type(e).__name__,
                error_message=str(e)[:200],
            )
            try:
                setattr(e, _LOGGED_MARK, True)
            except AttributeError:
                pass  # exception type without attributes; outer blocks log again
        
        # Re-raise
        raise
        
    finally:
        # Add timing metadata
        duration_ms = (time.time() - start_time) * 1000
        
        if is_xray_enabled():
            xray.put_metadata('timing', 'duration_ms', duration_ms)
        
        # End X-Ray subsegment
        if is_xray_enabled():
            xray.end_subsegment()
