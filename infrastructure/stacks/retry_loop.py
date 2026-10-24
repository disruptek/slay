"""
The retry loop of the slay-retry state machine, as a CDK helper.

The execution input controls the loop (see DESIGN.md, section 10):

    {"index": <the item's index in a fan-out; 0 in slay-retry>,
     "call": <encoded call>,
     "retry": {"waits": [seconds, ...], "retry_on": [classes], "timeout": seconds},
     "attempt": 0}

The loop always ends in success. Its output is {"index": ..., "response":
...}: the handler's response, or {"status": "lambda_error", "errorType":
..., "errorMessage": ...} for an attempt that timed out or crashed. A Fail
state cannot re-emit an error name such as States.Timeout, and a Map item
processor must report every item. The index orders a fan-out's results,
which the Map's ResultWriter writes in no particular order.
"""

from aws_cdk import Duration
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

# Error names that mean the attempt ran out of time
TIMEOUT_ERRORS = ("States.Timeout", "Sandbox.Timedout")


def retry_loop(scope: Construct, construct_id: str, executor: lambda_.IFunction) -> sfn.IChainable:
    """
    Build the loop's states under a new construct and return its first state.

    Args:
        scope: Where the states go
        construct_id: Id of the construct that holds the states
        executor: The Lambda each attempt invokes
    """
    loop = Construct(scope, construct_id)

    attempt = tasks.LambdaInvoke(
        loop,
        "Attempt",
        lambda_function=executor,
        payload=sfn.TaskInput.from_json_path_at("$.call"),
        result_selector={"Payload": sfn.JsonPath.object_at("$.Payload")},
        result_path="$.result",
        task_timeout=sfn.Timeout.at("$.retry.timeout"),
        retry_on_service_exceptions=True,
    )
    # Throttling is not an attempt: retry it here, before the loop sees it
    attempt.add_retry(
        errors=["Lambda.TooManyRequestsException"],
        interval=Duration.seconds(1),
        max_attempts=6,
        backoff_rate=2,
    )

    def error_class(name: str) -> sfn.Pass:
        return sfn.Pass(
            loop, name.capitalize(), result=sfn.Result.from_string(name), result_path="$.class"
        )

    failure, timeout, crash = error_class("failure"), error_class("timeout"), error_class("crash")

    def report(name: str, response: dict) -> sfn.Pass:
        """A final state whose output is the index and this response"""
        return sfn.Pass(
            loop,
            name,
            parameters={"index": sfn.JsonPath.number_at("$.index"), "response": response},
        )

    returned = report("Returned", sfn.JsonPath.object_at("$.result.Payload"))
    raised = sfn.Choice(loop, "Raised?")
    raised.when(sfn.Condition.string_equals("$.result.Payload.status", "error"), failure)
    raised.otherwise(returned)

    caught = sfn.Choice(loop, "Error class")
    caught.when(
        sfn.Condition.or_(
            *(sfn.Condition.string_equals("$.caught.Error", name) for name in TIMEOUT_ERRORS)
        ),
        timeout,
    )
    caught.otherwise(crash)
    attempt.add_catch(caught, errors=["States.ALL"], result_path="$.caught")

    check = sfn.Pass(
        loop,
        "Check retry",
        parameters={
            "retryable": sfn.JsonPath.array_contains(
                sfn.JsonPath.list_at("$.retry.retry_on"), sfn.JsonPath.string_at("$.class")
            ),
            "waits": sfn.JsonPath.array_length(sfn.JsonPath.list_at("$.retry.waits")),
        },
        result_path="$.check",
    )
    for error in (failure, timeout, crash):
        error.next(check)

    next_attempt = sfn.Pass(
        loop,
        "Next attempt",
        parameters={
            "index": sfn.JsonPath.number_at("$.index"),
            "call": sfn.JsonPath.object_at("$.call"),
            "retry": sfn.JsonPath.object_at("$.retry"),
            "attempt": sfn.JsonPath.math_add(sfn.JsonPath.number_at("$.attempt"), 1),
            "wait": sfn.JsonPath.array_get_item(
                sfn.JsonPath.list_at("$.retry.waits"), sfn.JsonPath.number_at("$.attempt")
            ),
        },
    )
    wait = sfn.Wait(loop, "Wait", time=sfn.WaitTime.seconds_path("$.wait"))
    next_attempt.next(wait)
    wait.next(attempt)

    raised_out = report("Raised", sfn.JsonPath.object_at("$.result.Payload"))
    lambda_error = report(
        "Lambda error",
        {
            "status": "lambda_error",
            "errorType": sfn.JsonPath.string_at("$.caught.Error"),
            "errorMessage": sfn.JsonPath.string_at("$.caught.Cause"),
        },
    )
    give_up = sfn.Choice(loop, "Give up")
    give_up.when(sfn.Condition.string_equals("$.class", "failure"), raised_out)
    give_up.otherwise(lambda_error)

    retry = sfn.Choice(loop, "Retry?")
    retry.when(
        sfn.Condition.and_(
            sfn.Condition.boolean_equals("$.check.retryable", True),
            sfn.Condition.number_less_than_json_path("$.attempt", "$.check.waits"),
        ),
        next_attempt,
    )
    retry.otherwise(give_up)
    check.next(retry)

    attempt.next(raised)
    return attempt
