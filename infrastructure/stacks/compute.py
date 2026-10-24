"""AWS CDK Compute Stack - Lambda, state machines, dead letters and schedules"""

import sys
from aws_cdk import (
    ArnFormat,
    AssetHashType,
    BundlingOptions,
    CfnOutput,
    Stack,
    Duration,
    Size,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_scheduler as scheduler,
    aws_sqs as sqs,
    aws_stepfunctions as sfn,
)
from constructs import Construct

from stacks.bundling import RUNTIME_DIR, LocalBundler
from stacks.fanout import RESULT_WRITER_V2_FLAG, fanout
from stacks.retry_loop import deferrable_loop

# Fixed names: the client builds the ARNs from its region and account
EXECUTOR_FUNCTION = "slay-executor"
RETRY_STATE_MACHINE = "slay-retry"
FANOUT_STATE_MACHINE = "slay-fanout"
SCHEDULE_GROUP = "slay"
SCHEDULER_ROLE = "slay-scheduler"
DEAD_LETTER_QUEUE = "slay-dead-letter"
# Every state machine name above starts with this
STATE_MACHINE_PREFIX = "slay-"


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

        # Before any construct is added: the fan-out's ResultWriterV2
        # renders only with this flag
        self.node.set_context(RESULT_WRITER_V2_FLAG, True)

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

        # Nested calls: the executor may invoke itself and start and watch
        # executions of slay's state machines. The ARNs come from the fixed
        # names; references to the resources would make the function, its
        # role and the state machines depend on each other.
        def arn(service: str, resource: str, name: str) -> str:
            return self.format_arn(
                service=service,
                resource=resource,
                resource_name=name,
                arn_format=ArnFormat.COLON_RESOURCE_NAME,
            )

        role.add_to_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[arn("lambda", "function", EXECUTOR_FUNCTION)],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["states:StartExecution"],
                resources=[arn("states", "stateMachine", f"{STATE_MACHINE_PREFIX}*")],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["states:DescribeExecution"],
                resources=[arn("states", "execution", f"{STATE_MACHINE_PREFIX}*:*")],
            )
        )

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
            function_name=EXECUTOR_FUNCTION,
            runtime=runtime,
            handler="handler.execute",
            code=code,
            timeout=Duration.minutes(14),  # 1 min buffer before Lambda limit
            memory_size=3008,  # Max memory = more CPU
            role=role,
            environment={
                "SLAY_STORAGE_BUCKET": storage_bucket.bucket_name,
                # Nested run() and apply() go to AWS, not to this process
                "SLAY_BACKEND": "lambda",
            },
            # Ephemeral storage for large intermediate results
            ephemeral_storage_size=Size.gibibytes(10),
            description="Slay remote function executor",
        )

        CfnOutput(self, "ExecutorFunction", value=self.executor.function_name)

        # The last failed attempt of a deferred or scheduled call
        self.dead_letters = sqs.Queue(
            self,
            "DeadLetters",
            queue_name=DEAD_LETTER_QUEUE,
            retention_period=Duration.days(14),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
        )

        CfnOutput(self, "DeadLetterQueue", value=self.dead_letters.queue_name)

        # Retried, deferred and scheduled calls: a loop that the execution
        # input controls, after an optional wait
        self.retry_machine = sfn.StateMachine(
            self,
            "RetryMachine",
            state_machine_name=RETRY_STATE_MACHINE,
            state_machine_type=sfn.StateMachineType.STANDARD,
            definition_body=sfn.DefinitionBody.from_chainable(
                deferrable_loop(self, "RetryLoop", self.executor, self.dead_letters)
            ),
        )

        CfnOutput(self, "RetryStateMachine", value=self.retry_machine.state_machine_name)

        # Schedules: clients create them in this group, and Scheduler
        # starts slay-retry through this role, which clients pass by name
        schedules = scheduler.CfnScheduleGroup(self, "Schedules", name=SCHEDULE_GROUP)
        scheduler_role = iam.Role(
            self,
            "SchedulerRole",
            role_name=SCHEDULER_ROLE,
            assumed_by=iam.ServicePrincipal(
                "scheduler.amazonaws.com",
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            ),
        )
        self.retry_machine.grant_start_execution(scheduler_role)

        CfnOutput(self, "ScheduleGroup", value=schedules.name)

        # Many calls: a Distributed Map whose children run the same loop
        self.fanout_machine = sfn.StateMachine(
            self,
            "FanoutMachine",
            state_machine_name=FANOUT_STATE_MACHINE,
            state_machine_type=sfn.StateMachineType.STANDARD,
            definition_body=sfn.DefinitionBody.from_chainable(
                fanout(self, "Fanout", self.executor, storage_bucket)
            ),
        )

        CfnOutput(self, "FanoutStateMachine", value=self.fanout_machine.state_machine_name)
