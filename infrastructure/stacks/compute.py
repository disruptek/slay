"""AWS CDK Compute Stack - Lambda functions for slay"""

import sys
from aws_cdk import (
    AssetHashType,
    BundlingOptions,
    CfnOutput,
    Stack,
    Duration,
    Size,
    aws_lambda as lambda_,
    aws_iam as iam,
)
from constructs import Construct

from stacks.bundling import RUNTIME_DIR, LocalBundler


def lambda_runtime_for(version_info) -> lambda_.Runtime:
    """
    Lambda Python runtime for a (major, minor, ...) version.

    The executor must run the clients' minor version, because dill values
    load only under the version that wrote them.

    Raises:
        ValueError: If Lambda offers no runtime for that version
    """
    major, minor = version_info[0], version_info[1]
    runtime = getattr(lambda_.Runtime, f"PYTHON_{major}_{minor}", None)
    if runtime is None:
        raise ValueError(
            f"Lambda has no Python {major}.{minor} runtime. Deploy, and run "
            f"the clients, with a Python version that Lambda supports."
        )
    return runtime


class ComputeStack(Stack):
    """Compute infrastructure for slay (Lambda functions)"""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        storage_bucket,
        **kwargs,
    ):
        super().__init__(scope, construct_id, **kwargs)

        # Lambda execution role
        role = iam.Role(
            self,
            "ExecutorRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )

        # Grant S3 access for storage bucket
        storage_bucket.grant_read_write(role)

        # The executor runs the deploying interpreter's Python minor version
        runtime = lambda_runtime_for(sys.version_info)
        python_version = "{}.{}".format(*sys.version_info[:2])

        # Bundle built locally; its hash follows the output, so a change
        # under src/slay produces a new deployment
        code = lambda_.Code.from_asset(
            str(RUNTIME_DIR),
            asset_hash_type=AssetHashType.OUTPUT,
            bundling=BundlingOptions(
                image=runtime.bundling_image,
                local=LocalBundler(python_version),
            ),
        )

        # Main executor function
        self.executor = lambda_.Function(
            self,
            "Executor",
            function_name="slay-executor",
            runtime=runtime,
            handler="handler.execute",
            code=code,
            timeout=Duration.minutes(14),  # 1 min buffer before Lambda limit
            memory_size=3008,  # Max memory = more CPU
            role=role,
            environment={
                "SLAY_STORAGE_BUCKET": storage_bucket.bucket_name,
            },
            # Ephemeral storage for large intermediate results
            ephemeral_storage_size=Size.gibibytes(10),
            description="Slay remote function executor",
        )

        CfnOutput(self, "ExecutorFunction", value=self.executor.function_name)
