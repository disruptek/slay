"""
Jobs: the work of apply() under an ID that outlives the client.

slay.start_apply() starts a job and slay.attach() finds one by its ID.
On Lambda the ID is the ARN of a slay-fanout execution; on the local
backend, a key in the memory of the process.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from slay.core.reference import Reference

if TYPE_CHECKING:
    from slay.backends.base import ExecutorBackend


@dataclass(frozen=True)
class Job:
    """
    A job of a backend.

    Attributes:
        id: The job ID; attach(id) finds the job again
        backend: The backend that runs the job
    """

    id: str
    backend: "ExecutorBackend"

    def status(self) -> str:
        """"RUNNING", "SUCCEEDED", "FAILED", "TIMED_OUT" or "ABORTED"."""
        return self.backend.job_status(self.id)

    def results(self) -> list[Reference]:
        """Wait for the job, then return or raise as apply() does"""
        return self.backend.job_results(self.id)
