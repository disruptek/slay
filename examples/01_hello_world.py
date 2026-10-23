"""
Hello World example with the new slay API.

Demonstrates:
1. Storing data with slay.put()
2. Retrieving data with slay.get()
3. Storing functions with slay.put()
4. Executing functions remotely

Usage:
    python examples/01_hello_world.py
"""

import slay


def main():
    print("=" * 60)
    print("slay: Distributed Computing Made Simple")
    print("=" * 60)
    print()

    # Example 1: Store and retrieve data
    print("Example 1: Store and Retrieve Data")
    print("-" * 60)
    ref = slay.put("hello world")
    print(f"ref = slay.put('hello world')")
    print(f"  → {ref}")
    print()

    data = slay.get(ref)
    print(f"data = slay.get(ref)")
    print(f"  → {data}")
    print()

    # Example 2: Store and execute a function
    print("Example 2: Remote Function Execution")
    print("-" * 60)
    fn = slay.put(lambda x: x * 2)
    print(f"fn = slay.put(lambda x: x * 2)")
    print(f"  → {fn}")
    print()

    result_ref = fn(21)
    print(f"result_ref = fn(21)")
    print(f"  → {result_ref}")
    print()

    result = result_ref.get()
    print(f"result = result_ref.get()")
    print(f"  → {result}")
    print()

    # Example 3: More complex function
    print("Example 3: Complex Function")
    print("-" * 60)

    def process(data):
        return f"Processed: {data.upper()}"

    fn = slay.put(process)
    print(f"fn = slay.put(process)")
    print()

    result_ref = fn("hello")
    print(f"result_ref = fn('hello')")
    result = result_ref.get()
    print(f"result = result_ref.get()")
    print(f"  → {result}")
    print()

    print("=" * 60)
    print("✅ All examples completed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    main()
