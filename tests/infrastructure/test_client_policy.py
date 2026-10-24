"""
The client policies against the AWS requests a client sends.

A recorder notes every request that slay sends from the test's thread
while backend auto-detection, run(), add_retry, apply(), a job and the
cache run in moto. IAM_POLICIES/SlayClient.json and IAM_POLICY.json must
allow each one. Requests from moto's state machine threads come from the
state machines' roles, not from the client, so the recorder skips them.
"""

import fnmatch
import json
import threading
from pathlib import Path

import boto3
import pytest
import slay
from slay.backends.lambda_ import LambdaBackend
from slay.core.context import Context
from synth import ACCOUNT, BUCKET, REGION, machine_arn

ROOT = Path(__file__).resolve().parents[2]

# Each policy file, and the text in it that stands for the account
POLICIES = {
    "SlayClient": ("IAM_POLICIES/SlayClient.json", "026792689263"),
    "IAM_POLICY": ("IAM_POLICY.json", "ACCOUNT_ID"),
}

# The IAM actions of the recorded requests. If the recorder sees fewer,
# the checks against the policies prove less than they claim.
CLIENT_ACTIONS = {
    "lambda:GetFunction",
    "lambda:InvokeFunction",
    "states:StartExecution",
    "states:DescribeExecution",
    "s3:GetObject",
    "s3:PutObject",
}

EXECUTOR = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:slay-executor"

# IAM limits, in characters without whitespace
USER_INLINE_LIMIT = 2048
GROUP_INLINE_LIMIT = 5120
MANAGED_LIMIT = 6144


def load_policy(name):
    """A policy file as a document, for the tests' account"""
    path, account = POLICIES[name]
    return json.loads((ROOT / path).read_text().replace(account, ACCOUNT))


def size(policy):
    """The characters IAM counts: the document without whitespace"""
    return len(json.dumps(policy, separators=(",", ":")))


def _listed(value):
    return [value] if isinstance(value, str) else value


def allows(policy, action, resource):
    """
    Whether an Allow statement of policy matches the action and the
    resource. The policy files use no Deny, NotAction, NotResource or
    Condition, so this checks that rather than evaluating them.
    """
    for statement in policy["Statement"]:
        assert set(statement) <= {"Sid", "Effect", "Action", "Resource"}, statement
        assert statement["Effect"] == "Allow", statement
        if any(fnmatch.fnmatch(action.lower(), pattern.lower()) for pattern in _listed(statement["Action"])) and any(
            fnmatch.fnmatchcase(resource, pattern) for pattern in _listed(statement["Resource"])
        ):
            return True
    return False


def _function(params):
    return f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{params['FunctionName']}"


def _object(params):
    return f"arn:aws:s3:::{params['Bucket']}/{params['Key']}"


# (service, operation) -> (IAM action, the resource ARN from the request
# parameters); None for an operation that needs no permission
IAM_REQUESTS = {
    ("sts", "GetCallerIdentity"): None,
    ("lambda", "GetFunction"): ("lambda:GetFunction", _function),
    ("lambda", "Invoke"): ("lambda:InvokeFunction", _function),
    ("states", "StartExecution"): ("states:StartExecution", lambda p: p["stateMachineArn"]),
    ("states", "DescribeExecution"): ("states:DescribeExecution", lambda p: p["executionArn"]),
    ("s3", "GetObject"): ("s3:GetObject", _object),
    ("s3", "HeadObject"): ("s3:GetObject", _object),
    ("s3", "PutObject"): ("s3:PutObject", _object),
    ("s3", "ListObjectsV2"): ("s3:ListBucket", lambda p: f"arn:aws:s3:::{p['Bucket']}"),
}


def iam_request(service, operation, params):
    """The (action, resource) that IAM checks for a request, or None"""
    assert (service, operation) in IAM_REQUESTS, (
        f"{service}:{operation} has no IAM action in this test; add it to IAM_REQUESTS"
    )
    entry = IAM_REQUESTS[(service, operation)]
    if entry is None:
        return None
    action, resource = entry
    return action, resource(params)


class Recorder:
    """The requests that boto3 clients send from the thread that made it"""

    def __init__(self):
        self.thread = threading.get_ident()
        self.requests = []

    def note(self, service, operation, params):
        if threading.get_ident() == self.thread:
            self.requests.append((service, operation, dict(params)))

    def on_parameters(self, params, model, **_):
        self.note(model.service_model.signing_name, model.name, params)


class RecordedLambda:
    """The Lambda stand-in, with each direct invoke recorded"""

    def __init__(self, stand_in, recorder):
        self.stand_in = stand_in
        self.recorder = recorder

    def invoke(self, **kwargs):
        self.recorder.note("lambda", "Invoke", kwargs)
        return self.stand_in.invoke(**kwargs)


def run_client(backend):
    """What a client does: every kind of call, with values stored in S3"""
    Context()._auto_detect_backend()
    large = "x" * 4096
    shout = slay.put(lambda text: text + "!")

    shout(large).get()
    slay.add_retry(shout, attempts=2, backoff=0)(large).get()
    slay.get_many(slay.apply(shout, [large, "y"]))

    job = slay.start_apply(shout, ["z"])
    slay.attach(job.id).status()
    job.results()

    cached = slay.add_caching(shout, version="1")
    cached(large).get()  # a miss: runs, then writes the entry
    cached(large).get()  # a hit: reads the entry, checks the result exists


@pytest.fixture
def client_requests(fanout, stand_in, monkeypatch):
    """The (action, resource) pairs of the requests run_client sends"""
    recorder = Recorder()
    events = boto3._get_default_session().events
    events.register("before-parameter-build", recorder.on_parameters)
    try:
        backend = LambdaBackend()
        backend.lambda_client = RecordedLambda(stand_in, recorder)
        monkeypatch.setattr(slay.api, "get_backend", lambda: backend)
        run_client(backend)
    finally:
        events.unregister("before-parameter-build", recorder.on_parameters)
    return {iam_request(*request) for request in recorder.requests} - {None}


@pytest.mark.parametrize("name", POLICIES)
def test_policy_allows_every_client_request(name, client_requests):
    policy = load_policy(name)

    assert {action for action, _ in client_requests} == CLIENT_ACTIONS
    assert sorted(request for request in client_requests if not allows(policy, *request)) == []


class TestSlayClient:
    """SlayClient.json grants the client's rights and no others"""

    def test_lists_the_bucket(self):
        """Without ListBucket, S3 reports a cache miss as access denied"""
        assert allows(load_policy("SlayClient"), "s3:ListBucket", f"arn:aws:s3:::{BUCKET}")

    @pytest.mark.parametrize(
        "action, resource",
        [
            ("lambda:InvokeFunction", f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:other"),
            ("lambda:UpdateFunctionCode", EXECUTOR),
            ("states:StartExecution", machine_arn("other")),
            ("states:StopExecution", f"arn:aws:states:{REGION}:{ACCOUNT}:execution:slay-fanout:x"),
            ("s3:DeleteObject", f"arn:aws:s3:::{BUCKET}/refs/ab/abc.bin"),
            ("s3:GetObject", "arn:aws:s3:::other-bucket/refs/ab/abc.bin"),
            ("iam:PassRole", f"arn:aws:iam::{ACCOUNT}:role/slay-executor"),
        ],
    )
    def test_grants_nothing_else(self, action, resource):
        assert not allows(load_policy("SlayClient"), action, resource)

    def test_fits_one_user(self):
        """A user may hold 2,048 characters of inline policy"""
        assert size(load_policy("SlayClient")) <= USER_INLINE_LIMIT


def test_modular_policies_fit_one_group():
    """manage-policies.sh puts all of them, SlayClient included, on one group"""
    files = sorted((ROOT / "IAM_POLICIES").glob("Slay*.json"))

    assert "SlayClient.json" in [path.name for path in files]
    assert sum(size(json.loads(path.read_text())) for path in files) <= GROUP_INLINE_LIMIT


def test_iam_policy_fits_a_managed_policy():
    """The guides create IAM_POLICY.json as a managed policy"""
    assert size(load_policy("IAM_POLICY")) <= MANAGED_LIMIT
