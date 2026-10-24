"""
slay - Distributed computing made simple

Transform any function into a scalable, resilient service with one line of code.

Core operations:
    - put(data_or_func): Store and get a reference
    - get(ref): Retrieve data from reference
    - run(fn, *args): Execute function (core operation)
    - apply(fn, items): Parallel execution over items
    - start_apply(fn, items) / attach(job_id): The same as a Job
    - run_later(fn, *args) / run_at(when, fn, *args): One call as a Job
    - schedule(name, expression, fn, *args): The call on a schedule;
      reschedule, unschedule and schedules manage them
    - Callable syntax: fn(arg) is sugar for run(fn, arg)

Usage:
    import slay
    
    # Store data
    ref = slay.put("some data")
    data = slay.get(ref)
    
    # Store and execute function
    fn = slay.put(lambda x: x + 1)
    result_ref = slay.run(fn, 5)  # Core operation
    result = result_ref.get()
    
    # Or use syntactic sugar (same as above)
    result_ref = fn(5)  # Calls run(fn, 5) internally
    result = result_ref.get()
    
    # Parallel execution
    fn = slay.put(lambda x: x * 2)
    result_refs = slay.apply(fn, [1, 2, 3, 4, 5])
    results = slay.get_many(result_refs)
    
     # Function composition
     cached_fn = slay.add_caching(fn)
     resilient_fn = slay.add_retry(cached_fn, attempts=5)
"""

from slay.core.reference import create_reference, Reference
from slay.core.context import set_backend, get_backend
from slay.api import put, get, get_many, run, apply, start_apply, attach
from slay.deferred import run_later, run_at, schedule, reschedule, unschedule, schedules
from slay.jobs import Job
from slay.wrappers import add_retry, add_caching, add_cache_only
from slay.exceptions import CacheMiss, RemoteExecutionError, SlayError

__version__ = "0.1.0"

__all__ = [
    # Core API
    "put",
    "get",
    "get_many",
    "run",
    "apply",
    "start_apply",
    "attach",
    "Job",
    # Deferred and scheduled calls
    "run_later",
    "run_at",
    "schedule",
    "reschedule",
    "unschedule",
    "schedules",
    # References
    "create_reference",
    "Reference",
    # Wrappers
    "add_retry",
    "add_caching",
    "add_cache_only",
    # Configuration
    "set_backend",
    "get_backend",
    # Exceptions
    "CacheMiss",
    "RemoteExecutionError",
    "SlayError",
]
