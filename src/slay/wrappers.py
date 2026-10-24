"""
Function composition wrappers for slay.

These wrappers enable progressive enhancement of functions with additional
behaviors (retry, caching, rate limiting, etc.) without modifying the
original function code.

Key insight: Each wrapper returns a new Reference, not a modified function.
The original function is unchanged.

add_retry, add_caching and add_cache_only return a Reference to a policy
object (Retried, Cached), not to a wrapped function. call() and call_many() recognize such
a Reference by its type_info, without fetching it, and run the policy with
the backend they are given. Any other function goes to the backend.
"""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Callable, Optional

from slay.core.reference import Reference, as_reference, create_reference
from slay.core.storage import open_store
from slay.core.wire import dict_to_ref, ref_to_dict
from slay.exceptions import CacheMiss

if TYPE_CHECKING:
    from slay.backends.base import ExecutorBackend

Call = tuple[list[Reference], dict[str, Reference]]

# Error classes of a failed attempt (see add_retry)
FAILURE, TIMEOUT, CRASH = "failure", "timeout", "crash"

# Threads for the cache reads and writes of one call_many
_CACHE_WORKERS = 16


def is_wrapper(func_ref: Reference) -> bool:
    """True for a Reference to a policy object from this module"""
    return func_ref.type_info.module == __name__


def call(
    func_ref: Reference,
    args: list[Reference],
    kwargs: dict[str, Reference],
    backend: "ExecutorBackend",
) -> Reference:
    """
    Run one call: a wrapper runs its policy, any other function goes to
    backend.execute.

    Returns:
        Reference to the result

    Raises:
        Exception: Whatever the policy or backend.execute raises
    """
    if is_wrapper(func_ref):
        return func_ref.get().call(args, kwargs, backend)
    return backend.execute(func_ref, args, kwargs)


def call_many(
    func_ref: Reference,
    args_list: list[Call],
    backend: "ExecutorBackend",
    max_concurrent: Optional[int] = None,
) -> list[Reference]:
    """
    Run many calls: a wrapper runs its policy, any other function goes to
    backend.execute_many. max_concurrent reaches execute_many either way.

    Returns:
        References to the results, in the order of args_list
    """
    if is_wrapper(func_ref):
        return func_ref.get().call_many(args_list, backend, max_concurrent)
    return backend.execute_many(func_ref, args_list, max_concurrent=max_concurrent)


def without_retry(func_ref: Reference) -> tuple[Reference, Optional["Retried"]]:
    """
    (inner, policy) for a Reference from add_retry, else (func_ref, None).
    A backend that runs many calls at once takes a Retried policy as its
    retry argument; any other wrapper goes to it as the function.
    """
    if is_wrapper(func_ref) and func_ref.type_info.type_name == Retried.__name__:
        policy = func_ref.get()
        return policy.inner, policy
    return func_ref, None


def add_retry(
    func_ref: Reference,
    attempts: int = 3,
    backoff: float = 1.0,
    retry_on_failure: bool = True,
    retry_on_timeout: bool = False,
    retry_on_crash: bool = False,
    timeout: Optional[int] = None,
) -> Reference:
    """
    Wrap function with retry logic.

    A failed attempt is retried, up to `attempts` attempts in all, after a
    wait of backoff * 2 ** (n - 1) seconds before attempt n + 1. On Lambda
    the attempts run in the slay-retry state machine, which can also retry
    a timeout or a crash; LocalBackend retries failures in the process.
    add_retry does not fetch the function.

    Args:
        func_ref: Reference to function to wrap
        attempts: Number of attempts, the first included (default 3)
        backoff: Base wait in seconds (default 1.0); Step Functions rounds
            each wait up to whole seconds
        retry_on_failure: Retry when the function raises (default True)
        retry_on_timeout: Retry when an attempt times out (default False)
        retry_on_crash: Retry when the Lambda fails outside the function
            (default False)
        timeout: Seconds per attempt on Lambda (default None: the Lambda's
            own timeout)

    Returns:
        New callable Reference with retry behavior

    Raises:
        ValueError: If attempts < 1, backoff < 0, or timeout is not a
            positive whole number or None
        Exception (when called): The last attempt's exception, of its own
            type, if all attempts fail
        RemoteExecutionError (when called): If the last attempt timed out
            or crashed

    Example:
        >>> fn = slay.put(lambda x: x / 1)  # Might fail
        >>> resilient = slay.add_retry(fn, attempts=5, backoff=2.0)
        >>> result_ref = resilient(x)  # Retries on failure
        >>> result = result_ref.get()
    """
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        raise ValueError(f"attempts must be a whole number of at least 1, not {attempts!r}")
    if backoff < 0:
        raise ValueError(f"backoff must not be negative, not {backoff!r}")
    if timeout is not None and (
        isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1
    ):
        raise ValueError(f"timeout must be a positive whole number or None, not {timeout!r}")

    return _policy_reference(
        Retried(
            inner=_without_value(func_ref),
            attempts=attempts,
            backoff=backoff,
            retry_on_failure=retry_on_failure,
            retry_on_timeout=retry_on_timeout,
            retry_on_crash=retry_on_crash,
            timeout=timeout,
        )
    )


def add_caching(
    func_ref: Reference, ttl: Optional[int] = None, version: Optional[str] = None
) -> Reference:
    """
    Wrap function with a result cache in the storage bucket.

    Every client and Lambda that uses the same SLAY_STORAGE_BUCKET shares
    the entries. The key is a hash of the function's hash, the argument
    hashes and the version. run() reads the entry before it calls the
    backend, so a hit makes no backend call. A miss runs the function and
    writes the entry. add_caching does not fetch the function.

    Args:
        func_ref: Reference to function to wrap
        ttl: Accepted and ignored
        version: Part of the key; change it to stop using older entries

    Returns:
        New callable Reference with caching behavior

    Raises:
        StorageError: If SLAY_STORAGE_BUCKET is not set, when called, or
            when created if the wrapper is 1 KB or more

    Example:
        >>> fn = slay.put(lambda x: x ** 2)
        >>> cached_fn = slay.add_caching(fn)
        >>> result1_ref = cached_fn(5)  # Computes and caches
        >>> result1 = result1_ref.get()
        >>> result2_ref = cached_fn(5)  # Reads the entry; no backend call
        >>> result2 = result2_ref.get()
    """
    return _policy_reference(Cached(inner=_without_value(func_ref), version=version))


def add_cache_only(func_ref: Reference, version: Optional[str] = None) -> Reference:
    """
    Wrap function to read the entries that add_caching writes, and nothing else.

    A hit returns the stored result. A miss raises CacheMiss and runs
    nothing. add_cache_only does not fetch the function.

    Args:
        func_ref: Reference to function to wrap
        version: Part of the key; must match the version add_caching used

    Returns:
        New callable Reference that only reads the cache

    Raises:
        CacheMiss (when called): If no entry exists, or its result expired
        StorageError (when called): If SLAY_STORAGE_BUCKET is not set
    """
    return _policy_reference(
        Cached(inner=_without_value(func_ref), version=version, cache_only=True)
    )


class _Policy:
    """A wrapper's policy: call and call_many run it through a backend"""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Run the wrapped call in this process and return its value"""
        from slay.backends.local import LocalBackend

        args_refs = [as_reference(arg) for arg in args]
        kwargs_refs = {name: as_reference(value) for name, value in kwargs.items()}
        return self.call(args_refs, kwargs_refs, LocalBackend()).get()


@dataclass(frozen=True)
class Retried(_Policy):
    """
    Policy of add_retry. The backend runs the attempts: call passes the
    policy to backend.execute as retry.

    Attributes:
        inner: The wrapped function's Reference
        attempts: Number of attempts, the first included
        backoff: Base wait in seconds
        retry_on_failure, retry_on_timeout, retry_on_crash: The error
            classes to retry
        timeout: Seconds per attempt, or None for the executor's own limit
    """

    inner: Reference
    attempts: int = 3
    backoff: float = 1.0
    retry_on_failure: bool = True
    retry_on_timeout: bool = False
    retry_on_crash: bool = False
    timeout: Optional[int] = None

    def waits(self) -> list[float]:
        """Seconds to wait before attempt 2, 3, ...: backoff * 2 ** n"""
        return [self.backoff * 2**n for n in range(self.attempts - 1)]

    def retry_on(self) -> list[str]:
        """The error classes to retry, in the order failure, timeout, crash"""
        flags = (
            (FAILURE, self.retry_on_failure),
            (TIMEOUT, self.retry_on_timeout),
            (CRASH, self.retry_on_crash),
        )
        return [name for name, retried in flags if retried]

    def call(
        self,
        args: list[Reference],
        kwargs: dict[str, Reference],
        backend: "ExecutorBackend",
    ) -> Reference:
        return backend.execute(self.inner, args, kwargs, retry=self)

    def call_many(
        self,
        args_list: list[Call],
        backend: "ExecutorBackend",
        max_concurrent: Optional[int] = None,
    ) -> list[Reference]:
        return backend.execute_many(
            self.inner, args_list, retry=self, max_concurrent=max_concurrent
        )


@dataclass(frozen=True)
class Cached(_Policy):
    """
    Policy of add_caching and add_cache_only.

    Attributes:
        inner: The wrapped function's Reference
        version: Part of the key
        cache_only: A miss raises CacheMiss instead of calling inner
    """

    inner: Reference
    version: Optional[str] = None
    cache_only: bool = False

    def call(
        self,
        args: list[Reference],
        kwargs: dict[str, Reference],
        backend: "ExecutorBackend",
    ) -> Reference:
        """Return the stored result, or call inner and store its result"""
        store = open_store()
        key = self._key(args, kwargs)
        hit = _lookup(store, key)
        if hit is not None:
            return hit
        if self.cache_only:
            raise CacheMiss(
                f"No cache entry for this call of function {self.inner.hash[:8]}"
            )
        result = call(self.inner, args, kwargs, backend)
        _record(store, key, result)
        return result

    def call_many(
        self,
        args_list: list[Call],
        backend: "ExecutorBackend",
        max_concurrent: Optional[int] = None,
    ) -> list[Reference]:
        """Look up every call, run each distinct miss once, keep the order"""
        store = open_store()
        keys = [self._key(args, kwargs) for args, kwargs in args_list]
        distinct = list(dict.fromkeys(keys))
        found = dict(zip(distinct, _threaded(lambda key: _lookup(store, key), distinct)))

        missing = [key for key in distinct if found[key] is None]
        if missing and self.cache_only:
            index = next(i for i, key in enumerate(keys) if found[key] is None)
            raise CacheMiss(
                f"No cache entry for item {index} of function {self.inner.hash[:8]}"
            )
        if missing:
            first_call = {}
            for key, args_kwargs in zip(keys, args_list):
                first_call.setdefault(key, args_kwargs)
            results = call_many(
                self.inner, [first_call[key] for key in missing], backend, max_concurrent
            )
            _threaded(lambda pair: _record(store, *pair), list(zip(missing, results)))
            found.update(zip(missing, results))

        return [found[key] for key in keys]

    def _key(self, args: list[Reference], kwargs: dict[str, Reference]) -> str:
        """SHA-256 of the function hash, the argument hashes and the version"""
        parts = {
            "function": self.inner.hash,
            "args": [ref.hash for ref in args],
            "kwargs": {name: ref.hash for name, ref in kwargs.items()},
            "version": self.version,
        }
        encoded = json.dumps(parts, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _entry_key(key: str) -> str:
    return f"cache/{key[:2]}/{key}.json"


def _lookup(store, key: str) -> Optional[Reference]:
    """The stored result for a key, or None if it is missing or expired"""
    data = store.get(_entry_key(key))
    if data is None:
        return None
    result = dict_to_ref(json.loads(data))
    if result.storage_tier == "s3" and not store.exists(result.s3_key):
        return None
    return result


def _record(store, key: str, result: Reference) -> None:
    """Store a result Reference as the entry for a key"""
    store.put(_entry_key(key), json.dumps(ref_to_dict(result)).encode("utf-8"))


def _threaded(fn: Callable[[Any], Any], items: list) -> list:
    """fn over items, in threads when there is more than one item"""
    if len(items) <= 1:
        return [fn(item) for item in items]
    with ThreadPoolExecutor(max_workers=min(len(items), _CACHE_WORKERS)) as pool:
        return list(pool.map(fn, items))


def _without_value(func_ref: Reference) -> Reference:
    """
    The same Reference without its fetched value. A policy pickles its
    inner Reference, and a fetched value would ride along and change the
    policy's hash.
    """
    return replace(func_ref)


def _policy_reference(policy: Any) -> Reference:
    """A Reference to a policy; the client keeps the policy, never fetches it"""
    ref = create_reference(policy)
    ref._set_cached(policy)
    return ref
