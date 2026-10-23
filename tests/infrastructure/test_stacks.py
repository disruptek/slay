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
from stacks.compute import ComputeStack, lambda_runtime_for  # noqa: E402
from stacks.storage import StorageStack  # noqa: E402

PYTHON = "{}.{}".format(*sys.version_info[:2])


@pytest.fixture(scope="module")
def templates(tmp_path_factory):
    """(storage, compute) templates, synthesized without bundling"""
    app = aws_cdk.App(
        outdir=str(tmp_path_factory.mktemp("cdk.out")),
        context={"aws:cdk:bundling-stacks": []},
    )
    env = aws_cdk.Environment(account="123456789012", region="us-east-1")
    storage = StorageStack(app, "SlayStorageStack", env=env)
    compute = ComputeStack(
        app, "SlayComputeStack", storage_bucket=storage.storage_bucket, env=env
    )
    return (
        assertions.Template.from_stack(storage),
        assertions.Template.from_stack(compute),
    )


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
