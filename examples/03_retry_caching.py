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


def main():
    # For local testing, use temp directory for storage
    tmpdir = tempfile.mkdtemp()
    os.environ["SLAY_STORAGE_BUCKET"] = tmpdir
    print("=" * 60)
    print("Function Composition: Retry & Caching")
    print("=" * 60)
    print()

    # Example 1: Retry without caching
    print("Example 1: Resilient Execution with Retry")
    print("-" * 60)

    attempts = []

    def flaky_operation(x: int) -> int:
        """Simulates a flaky operation: the first two attempts of a call fail."""
        attempts.append(x)
        if len(attempts) < 3:
            raise Exception("Simulated failure")
        return x * 2

    fn = slay.put(flaky_operation)
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

    fn = slay.put(flaky_operation)
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
    
    # Cleanup
    import shutil
    shutil.rmtree(tmpdir)


if __name__ == "__main__":
    main()
