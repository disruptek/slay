"""
Function composition wrappers for slay.

These wrappers enable progressive enhancement of functions with additional
behaviors (retry, caching, rate limiting, etc.) without modifying the
original function code.

Key insight: Each wrapper returns a new Reference, not a modified function.
The original function is unchanged.
"""

import time
import functools
from typing import Optional

from slay.core.reference import Reference, create_reference


def add_retry(
    func_ref: Reference, attempts: int = 3, backoff: float = 1.0
) -> Reference:
    """
    Wrap function with retry logic.

    If the function fails, it will be retried up to `attempts` times with
    exponential backoff between attempts.

    Args:
        func_ref: Reference to function to wrap
        attempts: Number of attempts before giving up (default 3)
        backoff: Base backoff time in seconds, used with exponential strategy
            (default 1.0). Formula: sleep(backoff * (2 ** attempt))

    Returns:
        New callable Reference with retry behavior

    Raises:
        RemoteExecutionError: If all attempts fail

    Example:
        >>> fn = slay.put(lambda x: x / 1)  # Might fail
        >>> resilient = slay.add_retry(fn, attempts=5, backoff=2.0)
        >>> result_ref = resilient(x)  # Retries on failure
        >>> result = result_ref.get()
    """
    # Get original function
    original_func = func_ref.data

    @functools.wraps(original_func)
    def wrapped(*args, **kwargs):
        last_error = None
        for attempt in range(attempts):
            try:
                return original_func(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < attempts - 1:
                    sleep_time = backoff * (2**attempt)
                    print(
                        f"[retry] Attempt {attempt + 1} failed, "
                        f"retrying in {sleep_time:.1f}s..."
                    )
                    time.sleep(sleep_time)

        # All attempts failed
        print(f"[retry] All {attempts} attempts failed")
        if last_error:
            raise last_error
        raise Exception("All retry attempts failed")

    # Return new callable reference
    return create_reference(wrapped)


def add_caching(func_ref: Reference, ttl: Optional[int] = None) -> Reference:
    """
    Wrap function with caching (memoization).

    Results are cached by function identity + arguments. Same arguments
    always return the cached result.

    Cache key is: hash(function_ref) + hash(args) + hash(kwargs)

    Args:
        func_ref: Reference to function to wrap
        ttl: Time-to-live in seconds (None = cache forever).
            Not implemented in MVP.

    Returns:
        New callable Reference with caching behavior

    Example:
        >>> fn = slay.put(lambda x: x ** 2)
        >>> cached_fn = slay.add_caching(fn)
        >>> result1_ref = cached_fn(5)  # Computes and caches
        >>> result1 = result1_ref.get()
        >>> result2_ref = cached_fn(5)  # Returns from cache (fast!)
        >>> result2 = result2_ref.get()
    """
    original_func = func_ref.data
    func_hash = func_ref.hash

    @functools.wraps(original_func)
    def wrapped(*args, **kwargs):
        # Create cache key
        args_ref = create_reference(args)
        kwargs_ref = create_reference(kwargs)
        cache_key = f"{func_hash}:{args_ref.hash}:{kwargs_ref.hash}"

        # Check cache (MVP: simple dict, Phase 2: DynamoDB)
        cache = _get_cache()
        if cache_key in cache:
            result = cache[cache_key]
            print(f"[cache] Hit: {cache_key[:16]}...")
            return result

        # Cache miss - execute
        print(f"[cache] Miss: {cache_key[:16]}...")
        result = original_func(*args, **kwargs)

        # Store in cache
        cache[cache_key] = result

        return result

    return create_reference(wrapped)


# Simple in-memory cache for MVP
_CACHE: dict = {}


def _get_cache() -> dict:
    """Get global cache dict"""
    return _CACHE


def clear_cache() -> None:
    """Clear all cached results"""
    _CACHE.clear()
    print("[cache] Cleared all cached results")


def cache_stats() -> dict:
    """Get cache statistics"""
    return {
        "entries": len(_CACHE),
        "keys": list(_CACHE.keys())[:10],  # First 10
    }
