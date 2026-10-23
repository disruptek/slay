#!/usr/bin/env python3
"""
AWS CDK app for slay infrastructure deployment.

Deploy with: cdk deploy

This creates:
- S3 bucket for data storage
- Lambda function for remote execution
- DynamoDB table for metadata (optional)
"""

import sys
import os

# Add parent directory to path so we can import stacks
sys.path.insert(0, os.path.dirname(__file__))

import aws_cdk as cdk

from stacks.storage import StorageStack
from stacks.compute import ComputeStack


app = cdk.App()

# Get AWS account and region from context or environment
account = app.node.try_get_context("account") or os.environ.get("CDK_DEFAULT_ACCOUNT") or os.environ.get("AWS_ACCOUNT_ID")
region = app.node.try_get_context("region") or os.environ.get("CDK_DEFAULT_REGION") or os.environ.get("AWS_REGION", "us-east-1")

if not account:
    raise ValueError(
        "AWS account ID must be provided. Set one of:\n"
        "  - CDK_DEFAULT_ACCOUNT environment variable\n"
        "  - AWS_ACCOUNT_ID environment variable\n"
        "  - cdk deploy -c account=123456789012"
    )

env = cdk.Environment(account=account, region=region)

# Storage stack (must be created first)
storage_stack = StorageStack(
    app,
    "SlayStorageStack",
    description="Slay storage infrastructure (S3 + DynamoDB)",
    env=env,
)

# Compute stack (depends on storage)
compute_stack = ComputeStack(
    app,
    "SlayComputeStack",
    storage_bucket=storage_stack.storage_bucket,
    description="Slay compute infrastructure (Lambda)",
    env=env,
)

# Make compute depend on storage
compute_stack.add_dependency(storage_stack)

app.synth()
