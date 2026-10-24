"""
Names and lookups for the synthesized templates of the slay stacks.

The tests synthesize with a fixed account and region, so every ARN in a
template resolves to plain text.
"""

import json

ACCOUNT = "123456789012"
REGION = "us-east-1"
EXECUTOR_ARN = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:slay-executor"
# The storage stack's bucket name, which crosses to the compute stack as
# an Fn::ImportValue
BUCKET = f"slay-storage-{ACCOUNT}-{REGION}"
PSEUDO_PARAMETERS = {
    "AWS::Partition": "aws",
    "AWS::Region": REGION,
    "AWS::AccountId": ACCOUNT,
    "AWS::URLSuffix": "amazonaws.com",
}


def machine_arn(name):
    """The ARN of the state machine with this name"""
    return f"arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:{name}"


def resolve(value):
    """
    A template value as plain text: Fn::Join joined, pseudo parameters
    filled in, every Fn::GetAtt taken as the executor's ARN (the only
    resource a slay definition names), and every Fn::ImportValue taken as
    the bucket's name, or its ARN for an export of an Arn attribute.
    """
    if isinstance(value, str):
        return value
    if "Fn::Join" in value:
        separator, parts = value["Fn::Join"]
        return separator.join(resolve(part) for part in parts)
    if "Fn::GetAtt" in value:
        return EXECUTOR_ARN
    if "Fn::ImportValue" in value:
        if "Arn" in value["Fn::ImportValue"]:
            return f"arn:aws:s3:::{BUCKET}"
        return BUCKET
    if "Ref" in value:
        return PSEUDO_PARAMETERS[value["Ref"]]
    raise ValueError(f"cannot resolve {value!r}")


def state_machine(template, name):
    """The resource of the state machine with this name"""
    machines = template.find_resources(
        "AWS::StepFunctions::StateMachine",
        {"Properties": {"StateMachineName": name}},
    )
    assert len(machines) == 1, f"no state machine named {name}"
    (machine,) = machines.values()
    return machine


def state_machine_definition(template, name):
    """The resolved ASL definition of the state machine with this name"""
    return json.loads(resolve(state_machine(template, name)["Properties"]["DefinitionString"]))


def role_grants(template, role_id):
    """
    The (action, resource) pairs that the policies attached to a role
    allow. A resource that does not resolve to text (a Ref to a resource
    of the stack) stays as its template value, in JSON.
    """
    grants = set()
    for policy in template.find_resources("AWS::IAM::Policy").values():
        properties = policy["Properties"]
        if {"Ref": role_id} not in properties.get("Roles", []):
            continue
        for statement in properties["PolicyDocument"]["Statement"]:
            if statement["Effect"] != "Allow":
                continue
            actions = statement["Action"]
            resources = statement["Resource"]
            for action in [actions] if isinstance(actions, str) else actions:
                for resource in resources if isinstance(resources, list) else [resources]:
                    grants.add((action, _text(resource)))
    return grants


def _text(value):
    try:
        return resolve(value)
    except (KeyError, ValueError):
        return json.dumps(value, sort_keys=True)
