"""
Jobs: the work of apply(), or of one call, under an ID that outlives the
client.

slay.start_apply() starts a job of many calls, slay.run_later() and
slay.run_at() a job of one call, and slay.attach() finds either by its
ID. On Lambda the ID is the ARN of a slay-fanout or a slay-retry
execution; on the local backend, a key in the memory of the process.
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
        """
        Wait for a job of start_apply(), then return or raise as apply()
        does. A job of one call raises ValueError.
        """
        return self.backend.job_results(self.id)

    def result(self) -> Reference:
        """
        Wait for a job of run_later() or run_at(), then return or raise as
        run() does. A job of start_apply() raises ValueError.
        """
        return self.backend.job_result(self.id)
