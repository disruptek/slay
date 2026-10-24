"""Executions of the state machines in moto, started and awaited"""

import time
import uuid

# Seconds that a test waits for an execution to end
DEADLINE = 60


def run_execution(sfn, machine_arn, document):
    """
    Start an execution with this input (JSON text) and a new name, wait
    for it to end, and return its description, which holds executionArn
    """
    execution = sfn.start_execution(
        stateMachineArn=machine_arn, name=uuid.uuid4().hex, input=document
    )["executionArn"]
    deadline = time.monotonic() + DEADLINE
    while True:
        described = sfn.describe_execution(executionArn=execution)
        if described["status"] != "RUNNING":
            return described
        assert time.monotonic() < deadline, "the execution did not end"
        time.sleep(0.05)
