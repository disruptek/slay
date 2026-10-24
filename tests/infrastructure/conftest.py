"""
Fixtures for the CDK stacks and the state machines they define.

Synth runs with bundling skipped. The Step Functions fixtures run a
synthesized definition in moto, whose ASL interpreter calls the
in-process Lambda stand-in. No fixture calls AWS.
"""

import copy
import json

import pytest

aws_cdk = pytest.importorskip("aws_cdk")

from aws_cdk import assertions  # noqa: E402

from lambda_stand_in import InProcessLambda  # noqa: E402
from stacks.compute import ComputeStack  # noqa: E402
from stacks.storage import StorageStack  # noqa: E402
from synth import ACCOUNT, BUCKET, REGION, state_machine_definition  # noqa: E402


@pytest.fixture(scope="session")
def templates(tmp_path_factory):
    """(storage, compute) templates, synthesized without bundling"""
    app = aws_cdk.App(
        outdir=str(tmp_path_factory.mktemp("cdk.out")),
        context={"aws:cdk:bundling-stacks": []},
    )
    env = aws_cdk.Environment(account=ACCOUNT, region=REGION)
    storage = StorageStack(app, "SlayStorageStack", env=env)
    compute = ComputeStack(
        app, "SlayComputeStack", storage_bucket=storage.storage_bucket, env=env
    )
    return (
        assertions.Template.from_stack(storage),
        assertions.Template.from_stack(compute),
    )


@pytest.fixture(scope="session")
def retry_definition(templates):
    """The slay-retry definition, as JSON text"""
    _, compute = templates
    return json.dumps(state_machine_definition(compute, "slay-retry"))


@pytest.fixture(scope="session")
def fanout_definition(templates):
    """The slay-fanout definition, as JSON text"""
    _, compute = templates
    return json.dumps(state_machine_definition(compute, "slay-fanout"))


@pytest.fixture
def stand_in():
    """The Lambda stand-in that state machine tasks and LambdaBackend call"""
    return InProcessLambda()


def _copy_without_executions(machine, memo):
    """
    Deep copy of a moto state machine, without its executions.

    moto's StartExecution deep-copies the state machine as a snapshot for
    the new execution. A finished execution holds a lock, which cannot be
    copied, so without this every second StartExecution fails.
    """
    cls = type(machine)
    clone = cls.__new__(cls)
    memo[id(machine)] = clone
    for name, value in machine.__dict__.items():
        setattr(clone, name, [] if name == "executions" else copy.deepcopy(value, memo))
    return clone


def _write_results(env, resource_runtime_part, state_credentials):
    """
    A Map's ResultWriter as AWS runs it. moto writes only the manifest;
    this also writes the child outputs to SUCCEEDED_<n>.json files and
    lists them in the manifest. Each entry's Output is JSON text.

    AWS keeps no order within or across the files. The entries therefore
    go in reverse order, split over two files when there are two or more,
    so a reader must use each output's index and read every file.
    """
    from moto.stepfunctions.parser.asl.component.state.exec.state_map.result_writer.resource_eval.resource_eval_s3 import (  # noqa: E501
        ResourceEvalS3,
    )

    parameters = env.stack.pop()
    outputs = env.stack.pop()
    s3 = ResourceEvalS3._get_s3_client(
        resource_runtime_part=resource_runtime_part, state_credentials=state_credentials
    )
    map_run_arn = env.map_run_record_pool_manager.get_all().pop().map_run_arn
    bucket = parameters["Bucket"]
    # moto's own layout: <prefix>/<map run id>/
    folder = "".join(
        f"{part}/" for part in (parameters["Prefix"].rstrip("/"), map_run_arn.split(":")[-1]) if part
    )

    entries = [{"Output": json.dumps(output), "Status": "SUCCEEDED"} for output in reversed(outputs)]
    half = (len(entries) + 1) // 2
    files = []
    for number, chunk in enumerate(part for part in (entries[:half], entries[half:]) if part):
        key = f"{folder}SUCCEEDED_{number}.json"
        body = json.dumps(chunk).encode("utf-8")
        s3.put_object(Bucket=bucket, Key=key, Body=body)
        files.append({"Key": key, "Size": len(body)})

    manifest_key = f"{folder}manifest.json"
    manifest = {
        "DestinationBucket": bucket,
        "MapRunArn": map_run_arn,
        "ResultFiles": {"FAILED": [], "PENDING": [], "SUCCEEDED": files},
    }
    s3.put_object(Bucket=bucket, Key=manifest_key, Body=json.dumps(manifest).encode("utf-8"))
    env.stack.append(
        {"MapRunArn": map_run_arn, "ResultWriterDetails": {"Bucket": bucket, "Key": manifest_key}}
    )


@pytest.fixture
def sfn(monkeypatch, retry_definition, stand_in):
    """
    A moto Step Functions client whose slay-retry state machine runs the
    synthesized definition. Its lambda:invoke tasks call stand_in. The
    stack's bucket exists in moto, and SLAY_STORAGE_BUCKET names it.
    """
    moto = pytest.importorskip("moto")
    pytest.importorskip("antlr4")
    import boto3
    from moto.stepfunctions.models import StateMachineInstance
    from moto.stepfunctions.parser.asl.component.state.exec.state_map.result_writer.resource_eval.resource_eval_s3 import (  # noqa: E501
        ResourceEvalS3,
    )
    from moto.stepfunctions.parser.asl.component.state.exec.state_task import (
        lambda_eval_utils,
    )

    monkeypatch.setattr(
        StateMachineInstance, "__deepcopy__", _copy_without_executions, raising=False
    )
    monkeypatch.setattr(ResourceEvalS3, "_handle_put_object", staticmethod(_write_results))

    for name in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)

    real_client_for = lambda_eval_utils.boto_client_for

    def client_for(**kwargs):
        if kwargs.get("service") == "lambda":
            return stand_in
        return real_client_for(**kwargs)

    monkeypatch.setattr(lambda_eval_utils, "boto_client_for", client_for)

    with moto.mock_aws(config={"stepfunctions": {"execute_state_machine": True}}):
        boto3.client("s3", region_name=REGION).create_bucket(Bucket=BUCKET)
        monkeypatch.setenv("SLAY_STORAGE_BUCKET", BUCKET)
        client = boto3.client("stepfunctions", region_name=REGION)
        client.create_state_machine(
            name="slay-retry",
            definition=retry_definition,
            roleArn=f"arn:aws:iam::{ACCOUNT}:role/slay-retry",
        )
        yield client


@pytest.fixture
def fanout(sfn, fanout_definition):
    """The sfn client, with the synthesized slay-fanout state machine too"""
    sfn.create_state_machine(
        name="slay-fanout",
        definition=fanout_definition,
        roleArn=f"arn:aws:iam::{ACCOUNT}:role/slay-fanout",
    )
    return sfn
