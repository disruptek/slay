"""
Hierarchical kill-switch for Slay executions.

Supports:
- Function-level kill: stop all executions of specific function
- User-level kill: stop all executions by user
- Execution-level kill: stop specific execution
- Global kill: emergency stop all executions

All kills are logged and graceful (finish current operation, don't start next).
"""

import threading
from typing import Optional

from slay.control.registry import get_registry
from slay.observability.logger import get_logger

logger = get_logger()

# Global kill switch (emergency only)
_global_kill_flag = False
_global_kill_lock = threading.Lock()


def _set_global_kill(enabled: bool) -> None:
    """Internal: set global kill flag"""
    global _global_kill_flag
    with _global_kill_lock:
        _global_kill_flag = enabled


def is_global_kill_active() -> bool:
    """Check if global kill is active"""
    with _global_kill_lock:
        return _global_kill_flag


def kill_function(function_hash: str, reason: str = "") -> None:
    """
    Kill all executions of a specific function.
    
    Args:
        function_hash: Function hash to kill
        reason: Reason for kill (logged)
    """
    registry = get_registry()
    
    # Find all active executions of this function
    active = registry.get_active_executions(function_hash=function_hash, status="running")
    
    count = 0
    for record in active:
        registry.set_kill_request(record.execution_id)
        count += 1
    
    logger.critical(
        "kill_function",
        function_hash=function_hash[:8],
        execution_count=count,
        reason=reason,
    )


def kill_user(user_id: str, reason: str = "") -> None:
    """
    Kill all executions by a specific user.
    
    Args:
        user_id: User ID to kill
        reason: Reason for kill (logged)
    """
    registry = get_registry()
    
    # Find all active executions by this user
    active = registry.get_active_executions(user_id=user_id, status="running")
    
    count = 0
    for record in active:
        registry.set_kill_request(record.execution_id)
        count += 1
    
    logger.critical(
        "kill_user",
        user_id=user_id,
        execution_count=count,
        reason=reason,
    )


def kill_execution(execution_id: str, reason: str = "") -> None:
    """
    Kill a specific execution.
    
    Args:
        execution_id: Execution ID to kill
        reason: Reason for kill (logged)
    """
    registry = get_registry()
    
    record = registry.get_execution(execution_id)
    if record:
        registry.set_kill_request(execution_id)
        logger.critical(
            "kill_execution",
            execution_id=execution_id,
            function_hash=record.function_hash[:8],
            reason=reason,
        )


def kill_all(reason: str = "") -> None:
    """
    Emergency global kill - stop all Slay executions.
    
    No confirmation required (as per user choice).
    Immediately sets global kill flag.
    
    Args:
        reason: Reason for kill (logged)
    """
    _set_global_kill(True)
    
    logger.critical(
        "GLOBAL_KILL",
        reason=reason,
    )
    
    # Also kill all currently active executions
    registry = get_registry()
    active = registry.get_active_executions(status="running")
    
    for record in active:
        registry.set_kill_request(record.execution_id)


def unkill_all() -> None:
    """Reset global kill flag (for testing/recovery)"""
    _set_global_kill(False)
    logger.info("unkill_all", message="Global kill flag reset")


def check_kill(execution_id: str) -> bool:
    """
    Check if execution should be killed.
    
    Called periodically during execution.
    Returns True if kill requested.
    
    Args:
        execution_id: Execution to check
    
    Returns:
        bool: True if kill requested, False otherwise
    """
    # Check global kill first
    if is_global_kill_active():
        return True
    
    # Check specific execution
    registry = get_registry()
    return registry.check_kill_request(execution_id)
