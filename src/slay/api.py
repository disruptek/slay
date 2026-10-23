"""
User-facing public API for slay distributed computing.

Main entry points:
- put(data_or_func): Store data or function, get a reference
- get(ref): Fetch data from a reference
- get_many(refs): Fetch multiple references in parallel
- run(fn, *args, **kwargs): Execute function with arguments (core operation)
- apply(fn, items): Apply function to multiple items in parallel

Note: Calling a function reference directly (fn(arg)) is syntactic sugar for run(fn, arg).
Both return a Reference to the result.
"""

from typing import Any

from slay.core.reference import Reference, create_reference
from slay.core.context import get_backend
from slay.observability.logger import get_logger
from slay.observability.trace_context import trace_operation

logger = get_logger()


def _as_reference(value: Any) -> Reference:
    """A Reference passes through as it is; any other value is stored"""
    if isinstance(value, Reference):
        return value
    return create_reference(value)


def put(data_or_func: Any) -> Reference:
    """
    Store data or function, return a reference.

    This is the primary way to get data into the slay system. Any
    serializable object can be stored.

    Args:
        data_or_func: Data or function to store (must be serializable)

    Returns:
        Reference to the stored object

    Raises:
        SerializationError: If object cannot be serialized
        StorageError: If storage fails

    Example:
        >>> # Store data
        >>> ref = slay.put("hello")
        >>> data = ref.get()
        >>> print(data)
        "hello"

        >>> # Store function
        >>> fn = slay.put(lambda x: x * 2)
        >>> result_ref = fn(21)
        >>> result = result_ref.get()
        >>> print(result)
        42
    """
    with trace_operation("slay.put", object_type=type(data_or_func).__name__):
        logger.info("put_start", object_type=type(data_or_func).__name__)
        ref = create_reference(data_or_func)
        logger.info("put_complete", ref_hash=ref.hash[:8], storage_tier=ref.storage_tier)
        return ref


def get(ref: Reference) -> Any:
    """
    Fetch data from a reference.

    Equivalent to ref.get(), provided for functional programming style.
    Results are cached locally after first fetch.

    Args:
        ref: Reference to fetch

    Returns:
        The deserialized data

    Raises:
        StorageError: If fetching fails
        SerializationError: If deserialization fails

    Example:
        >>> ref = slay.put("data")
        >>> data = slay.get(ref)
        >>> print(data)
        "data"
    """
    with trace_operation("slay.get", ref_hash=ref.hash[:8], storage_tier=ref.storage_tier):
        logger.info("get_start", ref_hash=ref.hash[:8])
        data = ref.get()
        logger.info("get_complete", ref_hash=ref.hash[:8])
        return data


def get_many(refs: list[Reference], max_workers: int = 10) -> list[Any]:
    """
    Fetch multiple references in parallel.

    If all references have cached data locally, returns immediately.
    Otherwise, fetches from storage in parallel using ThreadPoolExecutor.

    Args:
        refs: List of references to fetch
        max_workers: Maximum concurrent fetch threads (default 10)

    Returns:
        List of deserialized data in same order as input

    Raises:
        StorageError: If any fetch fails
        SerializationError: If deserialization fails

    Example:
        >>> ref1 = slay.put("data1")
        >>> ref2 = slay.put("data2")
        >>> data1, data2 = slay.get_many([ref1, ref2])
        >>> print(data1, data2)
        "data1" "data2"
    """
    with trace_operation("slay.get_many", ref_count=len(refs), max_workers=max_workers):
        logger.info("get_many_start", ref_count=len(refs))
        
        if not refs:
            logger.info("get_many_complete", ref_count=0, cached=True)
            return []

        # Check if all have cached data
        if all(ref._is_cached() for ref in refs):
            logger.info("get_many_complete", ref_count=len(refs), cached=True)
            return [ref.get() for ref in refs]

        # Fetch all references in parallel
        from slay.core.storage import load_many
        data_list = load_many(refs, max_workers=max_workers)

        # Cache all results locally for future calls
        for ref, data in zip(refs, data_list):
            ref._set_cached(data)

        logger.info("get_many_complete", ref_count=len(refs), cached=False)
        return data_list


def run(fn: Reference, *args, **kwargs) -> Reference:
    """
    Execute a function reference remotely and return a reference to the result.

    This is the core execution primitive. It returns a Reference, not the actual
    result. Callable syntax is the same call: fn(arg) is run(fn, arg).

    An argument that is already a Reference goes to the backend as it is,
    positional or keyword. The backend fetches its value and calls the
    function with that value, so results chain without a download. A
    Reference inside a container is not resolved.

    Args:
        fn: Function reference (from slay.put(function))
        *args: Positional arguments to pass to the function
        **kwargs: Keyword arguments to pass to the function

    Returns:
        Reference to the result (use .get() to deserialize)

    Raises:
        RemoteExecutionError: If execution fails
        SerializationError: If arguments cannot be serialized

    Example:
        >>> fn = slay.put(lambda x: x * 2)
        >>> result_ref = slay.run(fn, 21)
        >>> result = result_ref.get()  # Deserialize the result
        >>> print(result)
        42

        >>> # Same call with callable syntax
        >>> result_ref = fn(21)
        >>> print(result_ref.get())
        42

        >>> # Chain results without downloading them
        >>> double = slay.put(lambda x: x * 2)
        >>> print(double(fn(21)).get())
        84
    """
    with trace_operation("slay.run", function_hash=fn.hash[:8], arg_count=len(args)):
        logger.info("run_start", function_hash=fn.hash[:8], arg_count=len(args), backend=get_backend().name)
        
        args_refs = [_as_reference(arg) for arg in args]
        kwargs_refs = {name: _as_reference(value) for name, value in kwargs.items()}

        # Execute remotely via backend
        backend = get_backend()
        result_ref = backend.execute(fn, args_refs, kwargs_refs)
        
        logger.info("run_complete", function_hash=fn.hash[:8], result_hash=result_ref.hash[:8])
        return result_ref


def apply(fn: Reference, items: list[Any]) -> list[Reference]:
    """
    Apply a function reference to multiple items in parallel.

    Executes the function remotely on each item and returns references
    to the results (not the actual values). Use get_many() to fetch the
    actual results. An item that is already a Reference goes to the
    backend as it is, like a Reference argument to run().

    Args:
        fn: Function reference (from slay.put(function))
        items: List of items to process

    Returns:
        List of References to results in same order as inputs

    Raises:
        RemoteExecutionError: If any execution fails (fail-fast)
        SerializationError: If items cannot be serialized

    Example:
        >>> fn = slay.put(lambda x: x * 2)
        >>> result_refs = slay.apply(fn, [1, 2, 3, 4, 5])
        >>> results = slay.get_many(result_refs)  # Deserialize
        >>> print(results)
        [2, 4, 6, 8, 10]
        
        >>> # Or use callable syntax for each item
        >>> results = [fn(item).get() for item in [1, 2, 3, 4, 5]]
        >>> print(results)
        [2, 4, 6, 8, 10]
    """
    with trace_operation("slay.apply", function_hash=fn.hash[:8], item_count=len(items)):
        logger.info("apply_start", function_hash=fn.hash[:8], item_count=len(items), backend=get_backend().name)
        
        if not items:
            logger.info("apply_complete", function_hash=fn.hash[:8], result_count=0)
            return []

        # Each item becomes a single-argument call
        args_list = [([_as_reference(item)], {}) for item in items]

        # Execute via backend
        backend = get_backend()
        result_refs = backend.execute_many(fn, args_list)
        
        logger.info("apply_complete", function_hash=fn.hash[:8], result_count=len(result_refs))
        return result_refs
