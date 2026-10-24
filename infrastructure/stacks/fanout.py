"""
The Distributed Map of the slay-fanout state machine, as a CDK helper.

The execution input names the calls and how to run them (see DESIGN.md,
section 11):

    {"items_key": "fanout/<execution name>/items.json",
     "func_ref": <wire reference>,
     "retry": {"waits": [...], "retry_on": [...], "timeout": seconds},
     "max_concurrency": <0 for no limit>,
     "item_count": <the number of items; only the client reads it>}

The items file in the bucket holds one {"args_refs", "kwargs_refs"} object
per call. Each item runs the retry loop as a Standard child execution,
and a ResultWriter writes the children's {index, response} outputs under
fanout/results/ in the bucket. No failure is tolerated, but a child fails
only for a Step Functions reason: the loop reports a function's error as
its output.
"""

from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_stepfunctions as sfn
from constructs import Construct

from stacks.retry_loop import retry_loop

# CDK renders a ResultWriterV2 only with this feature flag on
RESULT_WRITER_V2_FLAG = "@aws-cdk/aws-stepfunctions:useDistributedMapResultWriterV2"

RESULTS_PREFIX = "fanout/results"


def fanout(
    scope: Construct, construct_id: str, executor: lambda_.IFunction, bucket: s3.IBucket
) -> sfn.DistributedMap:
    """
    Build the Map under a new construct and return it.

    Args:
        scope: Where the states go; RESULT_WRITER_V2_FLAG must be on there
        construct_id: Id of the construct that holds the states
        executor: The Lambda each attempt invokes
        bucket: The storage bucket, for the items file and the results
    """
    group = Construct(scope, construct_id)
    item = "$$.Map.Item.Value"

    fan_out = sfn.DistributedMap(
        group,
        "Fan-out",
        item_reader=sfn.S3JsonItemReader(
            bucket=bucket, key=sfn.JsonPath.string_at("$.items_key")
        ),
        item_selector={
            "index": sfn.JsonPath.number_at("$$.Map.Item.Index"),
            "call": {
                "func_ref": sfn.JsonPath.object_at("$.func_ref"),
                "args_refs": sfn.JsonPath.list_at(f"{item}.args_refs"),
                "kwargs_refs": sfn.JsonPath.object_at(f"{item}.kwargs_refs"),
            },
            "retry": sfn.JsonPath.object_at("$.retry"),
            "attempt": 0,
        },
        max_concurrency_path="$.max_concurrency",
        result_writer_v2=sfn.ResultWriterV2(bucket=bucket, prefix=RESULTS_PREFIX),
    )
    fan_out.item_processor(
        retry_loop(group, "Loop", executor),
        mode=sfn.ProcessorMode.DISTRIBUTED,
        execution_type=sfn.ProcessorType.STANDARD,
    )
    return fan_out
