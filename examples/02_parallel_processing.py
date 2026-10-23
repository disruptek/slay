"""
Parallel processing example with the new slay API.

Demonstrates:
1. Using slay.apply() for parallel execution
2. Using slay.get_many() to fetch results in parallel
3. Processing large datasets efficiently

Usage:
    python examples/02_parallel_processing.py
"""

import os
import tempfile
import slay


def main():
    # For local testing, use temp directory for storage
    tmpdir = tempfile.mkdtemp()
    os.environ["SLAY_STORAGE_BUCKET"] = tmpdir
    
    print("=" * 60)
    print("Parallel Processing with slay")
    print("=" * 60)
    print()

    # Example 1: Simple parallel map
    print("Example 1: Parallel Square Computation")
    print("-" * 60)

    fn = slay.put(lambda x: x ** 2)
    print(f"fn = slay.put(lambda x: x ** 2)")
    print()

    items = [1, 2, 3, 4, 5]
    print(f"items = {items}")
    print()

    result_refs = slay.apply(fn, items)
    print(f"result_refs = slay.apply(fn, items)")
    print(f"  → {len(result_refs)} references to results")
    print()

    results = slay.get_many(result_refs)
    print(f"results = slay.get_many(result_refs)")
    print(f"  → {results}")
    print()

    # Example 2: Processing with a more complex function
    print("Example 2: Parallel String Processing")
    print("-" * 60)

    def process_string(s):
        return f"[{len(s)}] {s.upper()}"

    fn = slay.put(process_string)
    strings = ["apple", "banana", "cherry", "date", "elderberry"]
    print(f"strings = {strings}")
    print()

    result_refs = slay.apply(fn, strings)
    results = slay.get_many(result_refs)
    print(f"results = slay.apply(fn, strings) |> slay.get_many()")
    for s, result in zip(strings, results):
        print(f"  {s:12} → {result}")
    print()

    # Example 3: Chaining operations
    print("Example 3: Chaining with Wrappers")
    print("-" * 60)

    # Add caching so repeated inputs are fast
    fn = slay.put(lambda x: x * 3)
    cached_fn = slay.add_caching(fn)
    print(f"fn = slay.put(lambda x: x * 3)")
    print(f"cached_fn = slay.add_caching(fn)")
    print()

    # Apply function and get results
    items = [1, 2, 3, 2, 1]  # Note: duplicate values
    print(f"items = {items}  (note: duplicates!)")
    print()

    result_refs = slay.apply(cached_fn, items)
    results = slay.get_many(result_refs)
    print(f"results = slay.apply(cached_fn, items) |> slay.get_many()")
    print(f"  → {results}")
    print()

    print("=" * 60)
    print("✅ Parallel processing completed successfully!")
    print("=" * 60)
    
    # Cleanup
    import shutil
    shutil.rmtree(tmpdir)


if __name__ == "__main__":
    main()
