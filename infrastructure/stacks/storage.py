"""AWS CDK Storage Stack - S3 buckets and DynamoDB tables for slay"""

from aws_cdk import (
    CfnOutput,
    Stack,
    Duration,
    RemovalPolicy,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
)
from constructs import Construct


class StorageStack(Stack):
    """Storage infrastructure for slay (S3 + DynamoDB)"""

    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # Main data storage bucket
        self.storage_bucket = s3.Bucket(
            self,
            "StorageBucket",
            bucket_name=f"slay-storage-{self.account}-{self.region}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            versioned=False,  # Content-addressed = no versioning needed
            lifecycle_rules=[
                s3.LifecycleRule(
                    # Transition to cheaper storage after 30 days
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INTELLIGENT_TIERING,
                            transition_after=Duration.days(30),
                        )
                    ],
                    # Delete after 90 days (adjust based on needs)
                    expiration=Duration.days(90),
                )
            ],
            # Prevent accidental deletion of data
            removal_policy=RemovalPolicy.RETAIN,
            # CORS for future web usage
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.GET, s3.HttpMethods.PUT],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                )
            ],
            public_read_access=False,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        # Clients set SLAY_STORAGE_BUCKET to this name. The construct id
        # "StorageBucket" belongs to the bucket, so only the output's
        # logical id carries that name.
        bucket_output = CfnOutput(
            self, "StorageBucketOutput", value=self.storage_bucket.bucket_name
        )
        bucket_output.override_logical_id("StorageBucket")

        # Optional: Metadata table (for future optimizations)
        self.metadata_table = dynamodb.Table(
            self,
            "MetadataTable",
            partition_key=dynamodb.Attribute(
                name="hash", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="ttl",
        )
