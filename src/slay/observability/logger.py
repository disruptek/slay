"""
Structured logging for Slay with adaptive sampling.

Features:
- Adaptive sampling (100% errors, 10% successes)
- CloudWatch Logs integration (auto-detect Lambda vs local)
- X-Ray trace ID injection for log correlation
- Thread-safe context tracking
- Hierarchical log levels
"""

import logging
import json
import time
import os
import threading
from datetime import datetime
from typing import Optional, Dict, Any
from collections import defaultdict

# Global logger instance
_logger: Optional['SlayLogger'] = None
_logger_lock = threading.Lock()


class SlayLogger:
    """
    Structured logger with adaptive sampling and observability.
    
    - Always logs errors (100%)
    - Samples successes at adaptive rate (10% baseline)
    - Increases sampling when error rate exceeds threshold
    - Injects X-Ray trace IDs for CloudWatch correlation
    """
    
    def __init__(self, service_name: str = "slay"):
        self.service_name = service_name
        self.is_lambda = 'AWS_LAMBDA_FUNCTION_NAME' in os.environ
        
        # Sampling configuration
        self.sampling_config = {
            'base_rate': 0.10,  # 10% baseline for successes
            'error_threshold_low': 0.05,  # 5% errors → increase to 50%
            'error_threshold_high': 0.20,  # 20% errors → increase to 100%
            'sample_rate_low': 0.50,  # Sampling rate when error_threshold_low exceeded
            'sample_rate_high': 1.0,  # Sampling rate when error_threshold_high exceeded
        }
        
        # Track error rates per function for adaptive sampling
        self.error_rates: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            'total': 0,
            'errors': 0,
            'last_updated': time.time(),
        })
        
        # Current log level
        self.log_level = logging.INFO
        
        # X-Ray trace ID context (thread-local)
        self._local = threading.local()
        
        # CloudWatch handler (lazy initialization)
        self._cloudwatch_handler = None
        self._cloudwatch_initialized = False
        
        # Set up Python logging
        self._setup_python_logging()
    
    def _setup_python_logging(self):
        """Configure Python logging infrastructure"""
        # Create logger
        self.python_logger = logging.getLogger(self.service_name)
        self.python_logger.setLevel(logging.DEBUG)
        
        # Prevent propagation to avoid duplicate logs
        self.python_logger.propagate = False
        
        # Remove any existing handlers
        self.python_logger.handlers.clear()
        
        # Add console handler for local development
        if not self.is_lambda:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(
                logging.Formatter(
                    '[%(levelname)s] %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S'
                )
            )
            self.python_logger.addHandler(console_handler)
    
    def _get_cloudwatch_handler(self):
        """Lazy-initialize CloudWatch handler (optional)"""
        if self.is_lambda and not self._cloudwatch_initialized:
            try:
                from slay.observability.cloudwatch_logs import CloudWatchLogsHandler
                self._cloudwatch_handler = CloudWatchLogsHandler()
                self._cloudwatch_initialized = True
            except ImportError:
                # X-Ray/CloudWatch libraries not installed
                self._cloudwatch_initialized = True
        return self._cloudwatch_handler
    
    def set_trace_id(self, trace_id: Optional[str]):
        """
        Set X-Ray trace ID for current context.
        
        This trace ID will be included in all subsequent logs
        until cleared, enabling correlation in CloudWatch.
        """
        if not hasattr(self._local, 'trace_id'):
            self._local.trace_id = None
        self._local.trace_id = trace_id
    
    def get_trace_id(self) -> Optional[str]:
        """Get current X-Ray trace ID"""
        return getattr(self._local, 'trace_id', None)
    
    def should_sample(self, function_hash: str, is_error: bool) -> bool:
        """
        Adaptive sampling decision.
        
        - Errors: always sampled (100%)
        - Successes: sampled based on error rate
        - High error rate: increase sampling of successes
        """
        if is_error:
            return True  # Always log errors
        
        # Track success/failure for this function
        stats = self.error_rates[function_hash]
        stats['total'] += 1
        
        # Calculate error rate
        if stats['total'] > 0:
            error_rate = stats['errors'] / stats['total']
        else:
            error_rate = 0.0
        
        # Determine sampling rate based on error rate
        if error_rate >= self.sampling_config['error_threshold_high']:
            sample_rate = self.sampling_config['sample_rate_high']
        elif error_rate >= self.sampling_config['error_threshold_low']:
            sample_rate = self.sampling_config['sample_rate_low']
        else:
            sample_rate = self.sampling_config['base_rate']
        
        # Probabilistic sampling
        import random
        return random.random() < sample_rate
    
    def _format_log_entry(
        self,
        level: str,
        message: str,
        extra: Dict[str, Any],
        timestamp: Optional[str] = None
    ) -> Dict[str, Any]:
        """Format structured log entry"""
        entry = {
            'timestamp': timestamp or datetime.utcnow().isoformat() + 'Z',
            'level': level,
            'message': message,
            'service': self.service_name,
        }
        
        # Add X-Ray trace ID if available
        trace_id = self.get_trace_id()
        if trace_id:
            entry['trace_id'] = trace_id
        
        # Add extra fields
        entry.update(extra)
        
        return entry
    
    def _should_output(self, level: str) -> bool:
        """Check if log level should be output"""
        level_map = {
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'ERROR': logging.ERROR,
            'CRITICAL': logging.CRITICAL,
        }
        return level_map.get(level, logging.INFO) >= self.log_level
    
    def _emit_log(self, entry: Dict[str, Any]):
        """Emit log entry to outputs"""
        if not self._should_output(entry['level']):
            return
        
        # Format as JSON
        json_str = json.dumps(entry, default=str)
        
        # Log to Python logging (stderr in Lambda, stdout locally)
        log_level = getattr(logging, entry['level'], logging.INFO)
        self.python_logger.log(log_level, json_str)
        
        # Send to CloudWatch if available
        handler = self._get_cloudwatch_handler()
        if handler:
            handler.emit(entry)
    
    def log(
        self,
        level: str,
        message: str,
        function_hash: Optional[str] = None,
        **extra
    ) -> bool:
        """
        Log a structured message.
        
        Args:
            level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            message: Log message
            function_hash: Optional function hash for adaptive sampling
            **extra: Additional fields to include in log
        
        Returns:
            bool: Whether the log was sampled (True = logged, False = sampled out)
        """
        # Apply adaptive sampling
        is_error = level in ('ERROR', 'CRITICAL')
        if function_hash and not is_error:
            if not self.should_sample(function_hash, is_error):
                return False
        
        # Update error count
        if is_error and function_hash:
            self.error_rates[function_hash]['errors'] += 1
        
        # Format and emit
        entry = self._format_log_entry(level, message, extra)
        self._emit_log(entry)
        
        return True
    
    def debug(self, message: str, function_hash: Optional[str] = None, **extra) -> bool:
        """Log debug message"""
        return self.log('DEBUG', message, function_hash, **extra)
    
    def info(self, message: str, function_hash: Optional[str] = None, **extra) -> bool:
        """Log info message"""
        return self.log('INFO', message, function_hash, **extra)
    
    def warning(self, message: str, function_hash: Optional[str] = None, **extra) -> bool:
        """Log warning message"""
        return self.log('WARNING', message, function_hash, **extra)
    
    def error(self, message: str, function_hash: Optional[str] = None, **extra) -> bool:
        """Log error message (always sampled)"""
        return self.log('ERROR', message, function_hash, **extra)
    
    def critical(self, message: str, function_hash: Optional[str] = None, **extra) -> bool:
        """Log critical message (always sampled)"""
        return self.log('CRITICAL', message, function_hash, **extra)


def get_logger() -> SlayLogger:
    """
    Get or create the global Slay logger instance.
    
    Thread-safe singleton.
    """
    global _logger
    
    if _logger is None:
        with _logger_lock:
            if _logger is None:
                _logger = SlayLogger()
    
    return _logger


def set_log_level(level: str):
    """Set global log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)"""
    logger = get_logger()
    level_map = {
        'DEBUG': logging.DEBUG,
        'INFO': logging.INFO,
        'WARNING': logging.WARNING,
        'ERROR': logging.ERROR,
        'CRITICAL': logging.CRITICAL,
    }
    logger.log_level = level_map.get(level.upper(), logging.INFO)
