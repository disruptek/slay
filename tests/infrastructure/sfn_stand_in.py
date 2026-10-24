"""A stand-in for the boto3 Step Functions client whose executions end at once"""


class EndedExecution:
    """
    Every execution it starts has already ended in status, with details
    (error, cause, input, output) in its description. started records the
    keyword arguments of each StartExecution.
    """

    def __init__(self, status, **details):
        self.status = status
        self.details = details
        self.started = []

    def start_execution(self, **kwargs):
        self.started.append(kwargs)
        execution = kwargs["stateMachineArn"].replace(":stateMachine:", ":execution:")
        return {"executionArn": f"{execution}:{kwargs['name']}"}

    def describe_execution(self, executionArn):
        return {"executionArn": executionArn, "status": self.status, **self.details}
