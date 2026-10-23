"""
Execution registry backed by DynamoDB (or local in-memory fallback).

Tracks all active executions with:
- Execution ID, function hash, user ID
- Status (running/completed/failed/killed/throttled)
- Kill/throttle requests
- Timing and cost tracking

Uses DynamoDB for Lambda, in-memory dict for local development.
"""

import os
import time
import threading
import uuid
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta


class ExecutionRecord:
    """Single execution record"""
    
    def __init__(
        self,
        execution_id: str,
        function_hash: str,
        user_id: str = "default",
        backend: str = "local",
        parent_execution_id: Optional[str] = None,
    ):
        self.execution_id = execution_id
        self.function_hash = function_hash
        self.user_id = user_id
        self.backend = backend
        self.parent_execution_id = parent_execution_id
        
        self.status = "running"  # running, completed, failed, killed, throttled
        self.kill_requested = False
        self.throttle_factor = 1.0  # 0.0 = paused, 1.0 = full speed
        
        self.started_at = datetime.utcnow()
        self.completed_at: Optional[datetime] = None
        
        self.trace_id: Optional[str] = None
        self.error_count = 0
        self.retry_count = 0
        self.cost_estimate_usd = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for DynamoDB"""
        return {
            'execution_id': self.execution_id,
            'function_hash': self.function_hash,
            'user_id': self.user_id,
            'backend': self.backend,
            'status': self.status,
            'kill_requested': self.kill_requested,
            'throttle_factor': self.throttle_factor,
            'started_at': self.started_at.isoformat(),
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'parent_execution_id': self.parent_execution_id,
            'trace_id': self.trace_id,
            'error_count': self.error_count,
            'retry_count': self.retry_count,
            'cost_estimate_usd': self.cost_estimate_usd,
            'ttl': int((datetime.utcnow() + timedelta(days=7)).timestamp()),
        }


class ExecutionRegistry:
    """
    Track execution state for control purposes.
    
    Uses DynamoDB in Lambda, in-memory dict locally.
    Provides:
    - Register new execution
    - Query kill/throttle requests
    - Update execution status
    - Query by function/user/status
    """
    
    def __init__(self):
        self.is_lambda = 'AWS_LAMBDA_FUNCTION_NAME' in os.environ
        self.client = None
        self.table_name = 'slay-executions'
        
        # Local in-memory registry (always available)
        self._local_registry: Dict[str, ExecutionRecord] = {}
        self._local_lock = threading.Lock()
        
        # Try to initialize DynamoDB
        if self.is_lambda:
            try:
                import boto3
                self.client = boto3.resource('dynamodb')
            except ImportError:
                pass
    
    def register_execution(
        self,
        function_hash: str,
        user_id: str = "default",
        backend: str = "local",
        parent_execution_id: Optional[str] = None,
    ) -> str:
        """
        Register new execution.
        
        Args:
            function_hash: Function reference hash
            user_id: User executing function
            backend: Backend used (local/lambda)
            parent_execution_id: Parent if nested call
        
        Returns:
            execution_id
        """
        execution_id = str(uuid.uuid4())
        
        record = ExecutionRecord(
            execution_id=execution_id,
            function_hash=function_hash,
            user_id=user_id,
            backend=backend,
            parent_execution_id=parent_execution_id,
        )
        
        # Store in local registry (always)
        with self._local_lock:
            self._local_registry[execution_id] = record
        
        # Try to store in DynamoDB
        if self.client:
            try:
                table = self.client.Table(self.table_name)
                table.put_item(Item=record.to_dict())
            except Exception:
                # Silently fail - local registry is authoritative
                pass
        
        return execution_id
    
    def get_execution(self, execution_id: str) -> Optional[ExecutionRecord]:
        """Get execution record"""
        with self._local_lock:
            return self._local_registry.get(execution_id)
    
    def update_status(
        self,
        execution_id: str,
        status: str,
        **extra
    ) -> None:
        """
        Update execution status.
        
        Args:
            execution_id: Execution to update
            status: New status (running/completed/failed/killed/throttled)
            **extra: Additional fields (cost_estimate_usd, error_count, etc.)
        """
        with self._local_lock:
            record = self._local_registry.get(execution_id)
        
        if not record:
            return
        
        record.status = status
        record.completed_at = datetime.utcnow() if status != "running" else None
        
        # Update extra fields
        for key, value in extra.items():
            if hasattr(record, key):
                setattr(record, key, value)
        
        # Update DynamoDB
        if self.client:
            try:
                table = self.client.Table(self.table_name)
                table.update_item(
                    Key={'execution_id': execution_id},
                    UpdateExpression='SET #s = :s, updated_at = :u',
                    ExpressionAttributeNames={'#s': 'status'},
                    ExpressionAttributeValues={
                        ':s': status,
                        ':u': datetime.utcnow().isoformat(),
                    }
                )
            except Exception:
                pass
    
    def set_kill_request(self, execution_id: str) -> None:
        """Request execution to be killed"""
        with self._local_lock:
            record = self._local_registry.get(execution_id)
        
        if record:
            record.kill_requested = True
    
    def check_kill_request(self, execution_id: str) -> bool:
        """Check if kill was requested for this execution"""
        with self._local_lock:
            record = self._local_registry.get(execution_id)
        
        if record:
            return record.kill_requested
        return False
    
    def set_throttle_factor(self, execution_id: str, factor: float) -> None:
        """
        Set throttle factor for execution.
        
        Args:
            execution_id: Execution to throttle
            factor: 0.0 = paused, 1.0 = full speed
        """
        with self._local_lock:
            record = self._local_registry.get(execution_id)
        
        if record:
            record.throttle_factor = max(0.0, min(1.0, factor))
    
    def get_throttle_factor(self, execution_id: str) -> float:
        """Get throttle factor for execution (0.0 to 1.0)"""
        with self._local_lock:
            record = self._local_registry.get(execution_id)
        
        if record:
            return record.throttle_factor
        return 1.0  # No throttle by default
    
    def get_active_executions(
        self,
        function_hash: Optional[str] = None,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[ExecutionRecord]:
        """Query executions by filters"""
        with self._local_lock:
            results = []
            for record in self._local_registry.values():
                if function_hash and record.function_hash != function_hash:
                    continue
                if user_id and record.user_id != user_id:
                    continue
                if status and record.status != status:
                    continue
                results.append(record)
            return results
    
    def cleanup_old_executions(self, days: int = 7) -> int:
        """Remove executions older than N days"""
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        with self._local_lock:
            to_remove = [
                exec_id for exec_id, record in self._local_registry.items()
                if record.completed_at and record.completed_at < cutoff
            ]
            
            for exec_id in to_remove:
                del self._local_registry[exec_id]
            
            return len(to_remove)


# Global registry instance
_registry: Optional[ExecutionRegistry] = None
_registry_lock = threading.Lock()


def get_registry() -> ExecutionRegistry:
    """Get or create global execution registry"""
    global _registry
    
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ExecutionRegistry()
    
    return _registry
