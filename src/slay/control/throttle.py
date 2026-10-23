"""
Graceful throttling for Slay executions (as per user choice).

Instead of killing executions when thresholds are exceeded,
gracefully throttle them by reducing execution rate.

Features:
- Set throttle factor per execution (0.0 = paused, 1.0 = full speed)
- Auto-throttle on threshold violations
- Exponential backoff when throttled
- Transparent to user code
"""

import time
import threading
from typing import Optional

from slay.control.registry import get_registry
from slay.observability.logger import get_logger

logger = get_logger()


def set_throttle(execution_id: str, factor: float, reason: str = "") -> None:
    """
    Set throttle factor for an execution.
    
    Args:
        execution_id: Execution to throttle
        factor: Throttle factor (0.0 = paused, 1.0 = full speed)
        reason: Reason for throttle (logged)
    """
    factor = max(0.0, min(1.0, factor))
    
    registry = get_registry()
    registry.set_throttle_factor(execution_id, factor)
    
    if factor < 1.0:
        logger.warning(
            "throttle_set",
            execution_id=execution_id,
            factor=factor,
            reason=reason,
        )
    else:
        logger.info("throttle_reset", execution_id=execution_id)


def get_throttle_factor(execution_id: str) -> float:
    """
    Get current throttle factor for execution.
    
    Args:
        execution_id: Execution to check
    
    Returns:
        Throttle factor (0.0 to 1.0)
    """
    registry = get_registry()
    return registry.get_throttle_factor(execution_id)


def apply_throttle(execution_id: str) -> None:
    """
    Apply throttle by sleeping if needed.
    
    Called at the beginning of each operation.
    Sleeps proportionally to throttle factor.
    
    Args:
        execution_id: Execution to throttle
    """
    factor = get_throttle_factor(execution_id)
    
    if factor >= 1.0:
        # No throttle
        return
    
    if factor <= 0.0:
        # Paused - sleep indefinitely (check every 100ms for resume)
        while factor <= 0.0:
            time.sleep(0.1)
            factor = get_throttle_factor(execution_id)
    else:
        # Partial throttle: calculate sleep time
        # throttle_factor = 0.5 means run 50% speed = sleep 50% of time
        # For each unit of work, sleep proportionally
        sleep_time = (1.0 - factor) * 1.0  # Base 1 second sleep when fully throttled
        time.sleep(sleep_time)


def auto_throttle_on_threshold(
    function_hash: str,
    violation_type: str,
    severity: str = "normal"
) -> None:
    """
    Automatically throttle function when threshold exceeded.
    
    Args:
        function_hash: Function that exceeded threshold
        violation_type: Type of violation (cost/error_rate/concurrent)
        severity: Violation severity (normal/severe)
    """
    registry = get_registry()
    
    # Determine throttle factor based on violation type and severity
    if violation_type == "cost":
        if severity == "severe":
            factor = 0.10  # 90% reduction (10% speed)
        else:
            factor = 0.25  # 75% reduction (25% speed)
        reason = "cost_threshold_exceeded"
    
    elif violation_type == "error_rate":
        if severity == "severe":
            factor = 0.05  # 95% reduction (5% speed)
        else:
            factor = 0.10  # 90% reduction (10% speed)
        reason = "high_error_rate"
    
    elif violation_type == "concurrent":
        if severity == "severe":
            factor = 0.25  # 75% reduction (25% speed)
        else:
            factor = 0.50  # 50% reduction (50% speed)
        reason = "max_concurrent_exceeded"
    
    else:
        return
    
    # Apply to all active executions of this function
    active = registry.get_active_executions(function_hash=function_hash, status="running")
    
    for record in active:
        set_throttle(record.execution_id, factor, reason)
    
    logger.warning(
        "auto_throttle_applied",
        function_hash=function_hash[:8],
        violation_type=violation_type,
        severity=severity,
        execution_count=len(active),
        factor=factor,
    )


def reset_throttle(function_hash: Optional[str] = None) -> int:
    """
    Reset throttle for all executions.
    
    Args:
        function_hash: If provided, only reset this function
    
    Returns:
        Number of executions reset
    """
    registry = get_registry()
    
    active = registry.get_active_executions(function_hash=function_hash, status="running")
    
    count = 0
    for record in active:
        if record.throttle_factor < 1.0:
            set_throttle(record.execution_id, 1.0, "manual_reset")
            count += 1
    
    logger.info(
        "throttle_reset_all",
        function_hash=function_hash[:8] if function_hash else None,
        execution_count=count,
    )
    
    return count
