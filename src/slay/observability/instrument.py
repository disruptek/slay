"""
Auto-instrumentation of third-party libraries for X-Ray (OPTIONAL).

When X-Ray SDK is available, automatically instruments:
- boto3: AWS SDK calls (S3, Lambda, DynamoDB, CloudWatch)
- botocore: Low-level AWS calls
- requests: HTTP client calls

If X-Ray SDK not available, this module is a no-op.
"""

from typing import List


def auto_instrument_libraries():
    """
    Auto-instrument common libraries for X-Ray tracing.
    
    Automatically patches:
    - boto3 (AWS SDK)
    - botocore (AWS SDK low-level)
    - requests (HTTP client)
    
    Safe to call even if X-Ray SDK not available.
    """
    try:
        from aws_xray_sdk.core import patch
        
        # Patch AWS SDK
        patch(['boto3', 'botocore'])
        
        # Patch HTTP library
        patch(['requests'])
        
    except ImportError:
        # X-Ray SDK not available - silently skip
        pass


# Auto-instrument on import
auto_instrument_libraries()
