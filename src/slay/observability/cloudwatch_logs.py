"""
CloudWatch Logs handler for Slay (optional).

Sends structured logs to CloudWatch Logs in Lambda environment.
Auto-detects Lambda and configures appropriate log group/stream.
Gracefully degrades if boto3 not available or not in Lambda.
"""

import os
import json
import threading
import time
from typing import Optional, List, Dict, Any
from datetime import datetime


class CloudWatchLogsHandler:
    """
    Send logs to CloudWatch Logs (Lambda environment only).
    
    - Auto-creates log group with 30-day retention
    - Batches logs for efficiency
    - Thread-safe
    - Graceful error handling
    """
    
    def __init__(self, batch_size: int = 100, flush_interval: float = 5.0):
        """
        Initialize CloudWatch Logs handler.
        
        Args:
            batch_size: Batch logs before flushing
            flush_interval: Flush logs every N seconds
        """
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        
        # Only available in Lambda
        self.is_lambda = 'AWS_LAMBDA_FUNCTION_NAME' in os.environ
        if not self.is_lambda:
            self.client = None
            return
        
        # Try to import boto3
        try:
            import boto3
            self.client = boto3.client('logs')
        except ImportError:
            self.client = None
            return
        
        # Get log group/stream names
        self.function_name = os.environ.get('AWS_LAMBDA_FUNCTION_NAME', 'unknown')
        self.request_id = os.environ.get('AWS_REQUEST_ID', 'unknown')
        self.log_group = f'/slay/executions/{self.function_name}'
        self.log_stream = f'{self.request_id[:8]}-{int(time.time())}'
        
        # Initialize log group and stream
        self._ensure_log_group_exists()
        self._ensure_log_stream_exists()
        
        # Batch for efficiency
        self.batch: List[Dict[str, Any]] = []
        self.batch_lock = threading.Lock()
        
        # Last flush time
        self.last_flush = time.time()
    
    def _ensure_log_group_exists(self):
        """Create log group if it doesn't exist (30-day retention)"""
        if not self.client:
            return
        
        try:
            self.client.create_log_group(logGroupName=self.log_group)
        except self.client.exceptions.ResourceAlreadyExistsException:
            pass
        except Exception:
            # Silently fail - logging should never block execution
            pass
        
        # Set retention policy (30 days)
        try:
            self.client.put_retention_policy(
                logGroupName=self.log_group,
                retentionInDays=30
            )
        except Exception:
            pass
    
    def _ensure_log_stream_exists(self):
        """Create log stream if it doesn't exist"""
        if not self.client:
            return
        
        try:
            self.client.create_log_stream(
                logGroupName=self.log_group,
                logStreamName=self.log_stream
            )
        except self.client.exceptions.ResourceAlreadyExistsException:
            pass
        except Exception:
            pass
    
    def emit(self, entry: Dict[str, Any]):
        """Add log entry to batch"""
        if not self.client:
            return
        
        with self.batch_lock:
            self.batch.append({
                'timestamp': int(datetime.utcnow().timestamp() * 1000),
                'message': json.dumps(entry, default=str),
            })
            
            # Auto-flush if batch full or interval exceeded
            if len(self.batch) >= self.batch_size:
                self._flush()
            elif time.time() - self.last_flush >= self.flush_interval:
                self._flush()
    
    def _flush(self):
        """Send batched logs to CloudWatch"""
        if not self.batch or not self.client:
            return
        
        try:
            self.client.put_log_events(
                logGroupName=self.log_group,
                logStreamName=self.log_stream,
                logEvents=self.batch,
            )
            self.batch.clear()
            self.last_flush = time.time()
        except Exception:
            # Silently fail - logging should never break execution
            self.batch.clear()
    
    def flush(self):
        """Manually flush pending logs"""
        with self.batch_lock:
            self._flush()
    
    def __del__(self):
        """Ensure logs are flushed on cleanup"""
        try:
            self.flush()
        except Exception:
            pass
