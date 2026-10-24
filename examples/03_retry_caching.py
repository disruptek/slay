"""
Function composition with retry and caching.

Demonstrates:
1. Creating function references
2. Wrapping with add_retry() for resilience
3. Wrapping with add_caching() for memoization
4. Composing multiple behaviors without changing the original function

Usage:
    python examples/03_retry_caching.py
"""

import os
import tempfile
import slay


def flaky(counter_path: str):
    """
    A flaky operation: the first two attempts fail.

    Each attempt runs a fresh copy of the function (on Lambda, in a fresh
    invocation), so the attempts are counted in a file, not in memory.
    """

    def flaky_operation(x: int) -> int:
        with open(counter_path, "a") as counter:
            counter.write("attempt\n")
        with open(counter_path) as counter:
            attempt = len(counter.readlines())
        if attempt < 3:
            raise Exception("Simulated failure")
        return x * 2

    return flaky_operation


def main():
    # For local testing, use a temporary directory for storage
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["SLAY_STORAGE_BUCKET"] = tmpdir
        run_examples(tmpdir)


def run_examples(tmpdir: str):
    print("=" * 60)
    print("Function Composition: Retry & Caching")
    print("=" * 60)
    print()

    # Example 1: Retry without caching
    print("Example 1: Resilient Execution with Retry")
    print("-" * 60)

    fn = slay.put(flaky(os.path.join(tmpdir, "example-1.count")))
    resilient_fn = slay.add_retry(fn, attempts=5, backoff=0.1)
    print(f"fn = slay.put(flaky_operation)")
    print(f"resilient_fn = slay.add_retry(fn, attempts=5, backoff=0.1)")
    print()

    print("Executing resilient_fn(10)...")
    result_ref = resilient_fn(10)
    result = result_ref.get()
    print(f"  → {result}")
    print()

    # Example 2: Caching without retry
    print("Example 2: Caching (Memoization)")
    print("-" * 60)

    def expensive_computation(x: int) -> int:
        """Simulate expensive computation."""
        import time
        time.sleep(0.1)
        return x ** 3

    fn = slay.put(expensive_computation)
    cached_fn = slay.add_caching(fn)
    print(f"fn = slay.put(expensive_computation)")
    print(f"cached_fn = slay.add_caching(fn)")
    print()

    print("First call: cached_fn(5)...")
    result1_ref = cached_fn(5)
    result1 = result1_ref.get()
    print(f"  → {result1}")
    print()

    print("Second call: cached_fn(5)...")
    result2_ref = cached_fn(5)
    result2 = result2_ref.get()
    print(f"  → {result2} (from cache, instant!)")
    print()

    # Example 3: Composing retry + caching
    print("Example 3: Composing Retry & Caching")
    print("-" * 60)

    fn = slay.put(flaky(os.path.join(tmpdir, "example-3.count")))
    resilient_fn = slay.add_retry(fn, attempts=5, backoff=0.1)
    resilient_cached_fn = slay.add_caching(resilient_fn)
    print(f"fn = slay.put(flaky_operation)")
    print(f"resilient_fn = slay.add_retry(fn, attempts=5, backoff=0.1)")
    print(f"resilient_cached_fn = slay.add_caching(resilient_fn)")
    print()

    print("First call: resilient_cached_fn(10)...")
    result1_ref = resilient_cached_fn(10)
    result1 = result1_ref.get()
    print(f"  → {result1}")
    print()

    print("Second call: resilient_cached_fn(10)...")
    result2_ref = resilient_cached_fn(10)
    result2 = result2_ref.get()
    print(f"  → {result2} (from cache!)")
    print()

    # Example 4: Parallel execution with wrappers
    print("Example 4: Parallel Execution with Composed Functions")
    print("-" * 60)

    fn = slay.put(lambda x: x * 2)
    cached_fn = slay.add_caching(fn)
    print(f"fn = slay.put(lambda x: x * 2)")
    print(f"cached_fn = slay.add_caching(fn)")
    print()

    items = [1, 2, 3, 2, 1]  # Note: duplicates!
    print(f"items = {items}  (note: duplicates for caching benefit)")
    print()

    result_refs = slay.apply(cached_fn, items)
    results = slay.get_many(result_refs)
    print(f"results = slay.apply(cached_fn, items) |> slay.get_many()")
    print(f"  → {results}")
    print()

    print("=" * 60)
    print("✅ Function composition examples completed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    main()
