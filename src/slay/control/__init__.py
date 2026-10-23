"""
Control systems for Slay: kill-switch, throttling, and resource management.

WARNING: NOT WIRED IN. These functions stop nothing. No slay code registers
executions or checks for kill or throttle requests. The registry lives in
the memory of one process: it writes records to DynamoDB inside Lambda but
never reads them back. kill_all() and the other kill functions log at
CRITICAL level and leave every running execution, local or on Lambda, as
it is. Do not rely on them to stop work or cost.

Intended design:
- Hierarchical kill-switch (function/user/execution/global)
- Graceful throttling (rate limiting without killing)
- Execution registry (DynamoDB-backed)
- Resource threshold monitoring
"""

from slay.control.registry import get_registry, ExecutionRegistry
from slay.control.kill_switch import kill_function, kill_user, kill_execution, kill_all
from slay.control.throttle import set_throttle, get_throttle_factor

__all__ = [
    "get_registry",
    "ExecutionRegistry",
    "kill_function",
    "kill_user",
    "kill_execution",
    "kill_all",
    "set_throttle",
    "get_throttle_factor",
]
