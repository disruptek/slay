"""
IAM_POLICIES/manage-policies.sh, run against a stub of the aws command.

The stub logs each command line and succeeds. list-group-policies and
list-user-policies print AWS_STUB_POLICIES; get-caller-identity prints an
account. No AWS calls.
"""

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "IAM_POLICIES" / "manage-policies.sh"

DEPLOYMENT = {
    "SlayBootstrap",
    "SlayS3Storage",
    "SlayLambdaCompute",
    "SlayIAMRoles",
    "SlayCloudFormation",
    "SlayECRAssets",
}
OPTIONAL = {"SlayDynamoDB"}
CLIENT = {"SlayClient"}

STUB = """#!/bin/bash
printf '%s\\n' "$*" >> "$AWS_STUB_LOG"
case "$1 $2" in
    "sts get-caller-identity") echo 123456789012 ;;
    "iam list-group-policies"|"iam list-user-policies") echo "$AWS_STUB_POLICIES" ;;
esac
exit 0
"""

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="manage-policies.sh needs bash and jq",
)


class Stub:
    """A directory with the aws stub, and the log it writes"""

    def __init__(self, directory):
        self.bin = directory / "bin"
        self.bin.mkdir()
        aws = self.bin / "aws"
        aws.write_text(STUB)
        aws.chmod(0o755)
        self.log = directory / "aws.log"

    def run(self, *args, policies=()):
        env = {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "AWS_STUB_LOG": str(self.log),
            "AWS_STUB_POLICIES": "\t".join(sorted(policies)),
        }
        return subprocess.run(
            ["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60
        )

    def policies(self, command, target_option, target):
        """The policy names that commands such as put-group-policy named"""
        names = set()
        lines = self.log.read_text().splitlines() if self.log.exists() else []
        for line in lines:
            words = shlex.split(line)
            if words[:2] != ["iam", command]:
                continue
            assert words[words.index(target_option) + 1] == target, line
            names.add(words[words.index("--policy-name") + 1])
        return names


@pytest.fixture
def stub(tmp_path):
    return Stub(tmp_path)


class TestApply:
    def test_puts_every_policy_on_the_group(self, stub):
        result = stub.run("apply", "-t", "slayers")

        assert result.returncode == 0, result.stdout + result.stderr
        assert stub.policies("put-group-policy", "--group-name", "slayers") == DEPLOYMENT | OPTIONAL | CLIENT

    def test_client_puts_only_the_client_policy(self, stub):
        result = stub.run("apply", "-t", "slay-users", "--client")

        assert result.returncode == 0, result.stdout + result.stderr
        assert stub.policies("put-group-policy", "--group-name", "slay-users") == CLIENT

    def test_client_for_one_user(self, stub):
        result = stub.run("apply", "-u", "alice", "--client")

        assert result.returncode == 0, result.stdout + result.stderr
        assert stub.policies("put-user-policy", "--user-name", "alice") == CLIENT


class TestVerify:
    def test_requires_the_client_policy(self, stub):
        result = stub.run("verify", "-t", "slayers", policies=DEPLOYMENT)

        assert result.returncode == 1
        assert "SlayClient (MISSING)" in result.stdout

    def test_passes_with_every_required_policy(self, stub):
        result = stub.run("verify", "-t", "slayers", policies=DEPLOYMENT | CLIENT)

        assert result.returncode == 0, result.stdout + result.stderr

    def test_client_requires_only_the_client_policy(self, stub):
        result = stub.run("verify", "-t", "slay-users", "--client", policies=CLIENT)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "SlayClient" in result.stdout
        assert not any(name in result.stdout for name in DEPLOYMENT)


class TestRemove:
    def test_client_removes_only_the_client_policy(self, stub):
        result = stub.run("remove", "-t", "slay-users", "--client")

        assert result.returncode == 0, result.stdout + result.stderr
        assert stub.policies("delete-group-policy", "--group-name", "slay-users") == CLIENT
