"""
Tests for the CDK stacks and the Lambda bundle.

Synth runs with bundling skipped, so only the bundle tests run pip. No test
calls AWS.
"""

import json
import subprocess
import sys

import pytest

aws_cdk = pytest.importorskip("aws_cdk")

from aws_cdk import assertions  # noqa: E402

from stacks.bundling import LocalBundler  # noqa: E402
from stacks.compute import lambda_runtime_for  # noqa: E402
from synth import (  # noqa: E402
    ACCOUNT,
    BUCKET,
    EXECUTOR_ARN,
    REGION,
    role_grants,
    state_machine,
)

PYTHON = "{}.{}".format(*sys.version_info[:2])


class TestLambdaRuntime:
    """The Lambda runs the deploying interpreter's Python minor version"""

    def test_runtime_for_version(self):
        """A supported version maps to that Lambda runtime"""
        assert lambda_runtime_for((3, 13)).name == "python3.13"

    def test_unsupported_version_raises(self):
        """A version Lambda does not offer fails with the version in the message"""
        with pytest.raises(ValueError, match="3.99"):
            lambda_runtime_for((3, 99))

    def test_function_uses_deploying_python(self, templates):
        """The synthesized function runs this interpreter's minor version"""
        _, compute = templates
        compute.has_resource_properties(
            "AWS::Lambda::Function",
            {
                "FunctionName": "slay-executor",
                "Handler": "handler.execute",
                "Runtime": f"python{PYTHON}",
            },
        )


class TestOutputs:
    """The stacks export the names that clients and the docs use"""

    def test_storage_bucket_output(self, templates):
        storage, _ = templates
        storage.has_output("StorageBucket", {})

    def test_executor_function_output(self, templates):
        _, compute = templates
        compute.has_output("ExecutorFunction", {})

    def test_retry_state_machine_output(self, templates):
        _, compute = templates
        compute.has_output("RetryStateMachine", {})

    def test_fanout_state_machine_output(self, templates):
        _, compute = templates
        compute.has_output("FanoutStateMachine", {})


class TestRetryStateMachine:
    """slay-retry: a Standard state machine that may invoke the executor"""

    def test_standard_state_machine_with_fixed_name(self, templates):
        _, compute = templates
        compute.has_resource_properties(
            "AWS::StepFunctions::StateMachine",
            {"StateMachineName": "slay-retry", "StateMachineType": "STANDARD"},
        )

    def test_role_may_invoke_executor(self, templates):
        """The state machine's role allows lambda:InvokeFunction on the executor"""
        _, compute = templates
        compute.has_resource_properties(
            "AWS::IAM::Policy",
            {
                "PolicyDocument": {
                    "Statement": assertions.Match.array_with(
                        [
                            assertions.Match.object_like(
                                {"Action": "lambda:InvokeFunction", "Effect": "Allow"}
                            )
                        ]
                    )
                }
            },
        )

    def test_attempt_timeout_and_throttling(self, retry_definition):
        """The Task reads its timeout from the input and retries throttling"""
        states = json.loads(retry_definition)["States"]
        tasks = [state for state in states.values() if state["Type"] == "Task"]

        assert len(tasks) == 1
        (task,) = tasks
        assert task["TimeoutSecondsPath"] == "$.retry.timeout"
        retried = {name for rule in task["Retry"] for name in rule["ErrorEquals"]}
        assert "Lambda.TooManyRequestsException" in retried
        assert [catch["ErrorEquals"] for catch in task["Catch"]] == [["States.ALL"]]


def _role_of(resource):
    """The logical id of the role in a resource's Role or RoleArn (Fn::GetAtt)"""
    properties = resource["Properties"]
    role = properties.get("RoleArn") or properties["Role"]
    return role["Fn::GetAtt"][0]


@pytest.fixture(scope="module")
def fanout_map(fanout_definition):
    """The one state of slay-fanout: its Map"""
    states = json.loads(fanout_definition)["States"]
    assert len(states) == 1, sorted(states)
    (state,) = states.values()
    return state


class TestFanoutStateMachine:
    """slay-fanout: a Standard state machine with one Distributed Map"""

    def test_standard_state_machine_with_fixed_name(self, templates):
        _, compute = templates
        compute.has_resource_properties(
            "AWS::StepFunctions::StateMachine",
            {"StateMachineName": "slay-fanout", "StateMachineType": "STANDARD"},
        )

    def test_distributed_map_of_standard_children(self, fanout_map):
        assert fanout_map["Type"] == "Map"
        assert fanout_map["ItemProcessor"]["ProcessorConfig"] == {
            "Mode": "DISTRIBUTED",
            "ExecutionType": "STANDARD",
        }

    def test_items_come_from_the_bucket(self, fanout_map):
        """The ItemReader reads the JSON file the input names"""
        reader = fanout_map["ItemReader"]

        assert reader["Resource"] == "arn:aws:states:::s3:getObject"
        assert reader["ReaderConfig"] == {"InputType": "JSON"}
        assert reader["Parameters"] == {"Bucket": BUCKET, "Key.$": "$.items_key"}

    def test_each_child_gets_a_retry_loop_input(self, fanout_map):
        """The ItemSelector builds the loop's input from the item and the execution input"""
        assert fanout_map["ItemSelector"] == {
            "index.$": "$$.Map.Item.Index",
            "call": {
                "func_ref.$": "$.func_ref",
                "args_refs.$": "$$.Map.Item.Value.args_refs",
                "kwargs_refs.$": "$$.Map.Item.Value.kwargs_refs",
            },
            "retry.$": "$.retry",
            "attempt": 0,
        }

    def test_each_child_runs_the_retry_loop(self, fanout_map, retry_definition):
        """The item processor holds the same states as slay-retry"""
        loop = json.loads(retry_definition)
        processor = fanout_map["ItemProcessor"]

        assert processor["StartAt"] == loop["StartAt"]
        assert processor["States"] == loop["States"]

    def test_results_go_to_the_bucket(self, fanout_map):
        writer = fanout_map["ResultWriter"]

        assert writer["Resource"] == "arn:aws:states:::s3:putObject"
        assert writer["Parameters"] == {"Bucket": BUCKET, "Prefix": "fanout/results"}

    def test_concurrency_comes_from_the_input(self, fanout_map):
        assert fanout_map["MaxConcurrencyPath"] == "$.max_concurrency"
        assert "MaxConcurrency" not in fanout_map

    def test_no_tolerated_failures(self, fanout_map):
        """The default of no tolerated failure applies; the grammar rejects an explicit 0"""
        assert not [key for key in fanout_map if key.startswith("ToleratedFailure")]

    def test_role_grants(self, templates):
        """The role may read the items, write the results, invoke the
        executor and start its own child executions"""
        _, compute = templates
        machine = state_machine(compute, "slay-fanout")
        (machine_id,) = compute.find_resources(
            "AWS::StepFunctions::StateMachine",
            {"Properties": {"StateMachineName": "slay-fanout"}},
        )
        grants = role_grants(compute, _role_of(machine))

        objects = f"arn:aws:s3:::{BUCKET}/*"
        assert ("s3:GetObject", objects) in grants
        assert ("s3:PutObject", objects) in grants
        assert ("lambda:InvokeFunction", EXECUTOR_ARN) in grants
        assert ("states:StartExecution", json.dumps({"Ref": machine_id})) in grants


class TestNestedCalls:
    """The executor sends nested calls to AWS"""

    def test_executor_uses_the_lambda_backend(self, templates):
        _, compute = templates
        compute.has_resource_properties(
            "AWS::Lambda::Function",
            {
                "FunctionName": "slay-executor",
                "Environment": {
                    "Variables": {
                        "SLAY_BACKEND": "lambda",
                        "SLAY_STORAGE_BUCKET": assertions.Match.any_value(),
                    }
                },
            },
        )

    def test_executor_may_invoke_itself_and_start_executions(self, templates):
        _, compute = templates
        functions = compute.find_resources(
            "AWS::Lambda::Function", {"Properties": {"FunctionName": "slay-executor"}}
        )
        (function,) = functions.values()
        grants = role_grants(compute, _role_of(function))

        states = f"arn:aws:states:{REGION}:{ACCOUNT}"
        assert ("lambda:InvokeFunction", EXECUTOR_ARN) in grants
        assert ("states:StartExecution", f"{states}:stateMachine:slay-*") in grants
        assert ("states:DescribeExecution", f"{states}:execution:slay-*:*") in grants


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    """A real Lambda bundle for this interpreter's version (runs pip)"""
    out = tmp_path_factory.mktemp("bundle")
    assert LocalBundler(PYTHON).try_bundle(str(out))
    return out


class TestBundle:
    """The bundle holds everything the handler imports"""

    def test_bundle_contents(self, bundle):
        """handler.py, every slay package and dill are in the bundle"""
        assert (bundle / "handler.py").is_file()
        for package in ("slay", "slay/control", "slay/core", "slay/observability", "dill"):
            assert (bundle / package / "__init__.py").is_file(), package

    def test_bundle_has_no_bytecode_caches(self, bundle):
        """Local __pycache__ directories stay out of the bundle"""
        assert not list((bundle / "slay").rglob("__pycache__"))

    def test_handler_runs_from_bundle_alone(self, bundle):
        """The handler runs with only the bundle and the stdlib on sys.path"""
        import slay
        from slay.core.wire import dict_to_ref, encode_call

        event = encode_call(slay.put(lambda x: x * 2), [slay.put(21)], {})
        script = (
            "import json, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import handler\n"
            "print(json.dumps(handler.execute(json.load(sys.stdin), None)))\n"
        )

        result = subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-c", script, str(bundle)],
            input=json.dumps(event),
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert result.returncode == 0, result.stderr[-2000:]
        response = json.loads(result.stdout.splitlines()[-1])
        assert response["status"] == "success", response
        assert dict_to_ref(response["result_ref"]).get() == 42
