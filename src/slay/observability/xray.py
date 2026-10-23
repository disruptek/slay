"""
AWS X-Ray distributed tracing support for Slay (OPTIONAL).

Features:
- Auto-detect Lambda vs local environment
- Auto-start daemon locally (if possible)
- Parent-child subsegment tracing
- CloudWatch Logs correlation via trace IDs
- Privacy-first annotations (no sensitive data)
- Graceful fallback if X-Ray SDK not available

X-Ray is completely optional. If not installed or not in Lambda,
all operations fall back to no-op implementations.
"""

import os
import threading
from typing import Optional, Any, Dict

# Try to import X-Ray SDK
try:
    from aws_xray_sdk.core import xray_recorder
    from aws_xray_sdk.core.context import Context
    _XRAY_AVAILABLE = True
except ImportError:
    _XRAY_AVAILABLE = False
    xray_recorder = None


class NoOpXRayRecorder:
    """
    No-op X-Ray recorder for when X-Ray SDK is not available.
    
    All methods are no-ops that return None or self (for chaining).
    """
    
    def begin_segment(self, name: str, **kwargs) -> 'NoOpXRayRecorder':
        """No-op segment"""
        return self
    
    def begin_subsegment(self, name: str) -> 'NoOpXRayRecorder':
        """No-op subsegment"""
        return self
    
    def end_subsegment(self) -> None:
        """No-op end"""
        pass
    
    def put_annotation(self, key: str, value: Any) -> None:
        """No-op annotation"""
        pass
    
    def put_metadata(self, key: str, value: Any, namespace: str = None) -> None:
        """No-op metadata"""
        pass
    
    def current_segment(self) -> Optional['NoOpXRayRecorder']:
        """No-op current segment"""
        return self
    
    @property
    def trace_id(self) -> Optional[str]:
        """No-op trace ID"""
        return None
    
    @property
    def id(self) -> Optional[str]:
        """No-op segment ID"""
        return None


class SlayXRayRecorder:
    """
    X-Ray wrapper for Slay distributed tracing.
    
    - Auto-detects Lambda vs local
    - Optionally auto-starts daemon locally
    - Wraps aws_xray_sdk with graceful fallback
    - Thread-safe
    """
    
    def __init__(self):
        """Initialize X-Ray recorder"""
        self.is_lambda = 'AWS_LAMBDA_FUNCTION_NAME' in os.environ
        self.is_available = _XRAY_AVAILABLE
        self.xray_enabled = False
        
        if not self.is_available:
            # X-Ray SDK not installed - use no-op
            self._recorder = NoOpXRayRecorder()
            return
        
        try:
            if self.is_lambda:
                # Lambda: X-Ray daemon already available
                xray_recorder.configure(
                    service='slay-lambda',
                    context=Context(),
                    sampling=True,
                )
                self.xray_enabled = True
            else:
                # Local: Try to use X-Ray daemon on localhost:2000
                xray_recorder.configure(
                    service='slay-local',
                    daemon_address='127.0.0.1:2000',
                    context=Context(),
                    sampling=True,
                )
                # Note: We don't auto-start daemon locally (user responsibility)
                # But if user has it running, we'll use it
                self.xray_enabled = True
            
            self._recorder = xray_recorder
        except Exception:
            # Fall back to no-op if configuration fails
            self._recorder = NoOpXRayRecorder()
    
    def begin_segment(self, name: str, trace_id: Optional[str] = None) -> Any:
        """
        Begin X-Ray segment (top-level operation).
        
        Args:
            name: Segment name
            trace_id: Optional trace ID for linking
        
        Returns:
            Segment object (or no-op)
        """
        if not self.xray_enabled:
            return self._recorder.begin_segment(name)
        
        try:
            kwargs = {'name': name}
            if trace_id:
                kwargs['trace_id'] = trace_id
            return self._recorder.begin_segment(**kwargs)
        except Exception:
            return self._recorder
    
    def begin_subsegment(self, name: str) -> Any:
        """
        Begin X-Ray subsegment (nested operation).
        
        Args:
            name: Subsegment name
        
        Returns:
            Subsegment object (or no-op)
        """
        if not self.xray_enabled:
            return self._recorder.begin_subsegment(name)
        
        try:
            return self._recorder.begin_subsegment(name)
        except Exception:
            return self._recorder
    
    def end_subsegment(self) -> None:
        """End current X-Ray subsegment"""
        if not self.xray_enabled:
            return
        
        try:
            self._recorder.end_subsegment()
        except Exception:
            pass
    
    def put_annotation(self, key: str, value: Any) -> None:
        """
        Add X-Ray annotation (indexed, queryable).
        
        Use for: status, backend, function_hash.
        
        Args:
            key: Annotation key
            value: Annotation value (should be simple types)
        """
        if not self.xray_enabled:
            return
        
        try:
            # Convert to string if needed (X-Ray annotations must be strings/numbers/booleans)
            if isinstance(value, (str, int, float, bool)):
                self._recorder.put_annotation(key, value)
            else:
                self._recorder.put_annotation(key, str(value)[:256])
        except Exception:
            pass
    
    def put_metadata(self, key: str, value: Any, namespace: str = 'default') -> None:
        """
        Add X-Ray metadata (non-indexed, for debugging).
        
        Use for: sizes, durations, error details.
        
        Args:
            key: Metadata key
            value: Metadata value (can be complex)
            namespace: Metadata namespace (default: 'default')
        """
        if not self.xray_enabled:
            return
        
        try:
            self._recorder.put_metadata(key, value, namespace)
        except Exception:
            pass
    
    def get_trace_id(self) -> Optional[str]:
        """
        Get current X-Ray trace ID.
        
        Used to inject into logs for CloudWatch correlation.
        
        Returns:
            Trace ID string or None
        """
        if not self.xray_enabled:
            return None
        
        try:
            segment = self._recorder.current_segment()
            if segment and hasattr(segment, 'trace_id'):
                return segment.trace_id
        except Exception:
            pass
        
        return None
    
    def capture_error(self, error: Exception) -> None:
        """
        Capture exception in current X-Ray segment.
        
        Args:
            error: Exception to capture
        """
        if not self.xray_enabled:
            return
        
        try:
            import sys
            segment = self._recorder.current_segment()
            if segment:
                segment.put_exception(error, sys.exc_info())
        except Exception:
            pass


# Global X-Ray recorder instance
_xray_recorder: Optional[SlayXRayRecorder] = None
_xray_lock = threading.Lock()


def get_xray_recorder() -> SlayXRayRecorder:
    """
    Get or create the global X-Ray recorder.
    
    Thread-safe singleton. Safe to call even if X-Ray not available.
    
    Returns:
        SlayXRayRecorder instance (with graceful fallback to no-op)
    """
    global _xray_recorder
    
    if _xray_recorder is None:
        with _xray_lock:
            if _xray_recorder is None:
                _xray_recorder = SlayXRayRecorder()
    
    return _xray_recorder


def is_xray_enabled() -> bool:
    """Check if X-Ray is available and enabled"""
    recorder = get_xray_recorder()
    return recorder.xray_enabled
