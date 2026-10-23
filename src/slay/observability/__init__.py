"""
Observability and monitoring for Slay.

Provides:
- Structured logging with adaptive sampling
- CloudWatch Logs integration
- Optional X-Ray distributed tracing
- Real-time metrics and cost tracking
- CloudWatch dashboards
- Execution registry and control

All X-Ray components are optional. If X-Ray SDK not installed,
gracefully degrades to no-op implementations.
"""

from slay.observability.logger import get_logger, set_log_level
from slay.observability.xray import get_xray_recorder, is_xray_enabled
from slay.observability.trace_context import trace_operation

# Auto-instrument libraries if X-Ray available
from slay.observability import instrument

__all__ = [
    # Logging
    "get_logger",
    "set_log_level",
    # X-Ray tracing (optional)
    "get_xray_recorder",
    "is_xray_enabled",
    "trace_operation",
]
