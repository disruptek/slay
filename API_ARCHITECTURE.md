# Slay API Architecture

## Core Design Principle

**Core Operations vs Syntactic Sugar**

The Slay API has a clear separation between core primitives and convenience syntax:

### Core Primitives (Explicit)

```python
# put() - Store data/functions
ref = slay.put(data)

# get() - Retrieve data
data = slay.get(ref)

# run() - Execute functions (THE core operation)
result_ref = slay.run(fn, arg1, arg2, kwarg=value)

# get_many() - Batch retrieval
results = slay.get_many([ref1, ref2, ref3])

# apply() - Parallel execution over items
result_refs = slay.apply(fn, items)
```

### Syntactic Sugar (Convenient)

```python
# Callable syntax - Sugar for run()
result_ref = fn(arg1, arg2, kwarg=value)
# ↓ Internally calls ↓
# result_ref = slay.run(fn, arg1, arg2, kwarg=value)
```

## Why This Architecture?

### 1. Clarity of Intent
- `slay.run(fn, args)` is explicit - "execute this function"
- `fn(args)` is intuitive - looks like normal Python
- Users can choose based on readability preference

### 2. Consistency with References
- Just as `put()` and `get()` are mirror operations
- `run()` is the primary operation for execution
- `fn(args)` is just a convenient way to call `run()`

### 3. Easy to Reason About
```python
# These are identical
result_ref = slay.run(fn, 5)
result_ref = fn(5)

# One is explicit about what's happening
# One is Pythonic and concise
# Both are equally valid
```

### 4. Consistent with Built-in Behavior
Python's model: `len(x)` calls `x.__len__()`
Slay's model: `fn(x)` calls `slay.run(fn, x)` internally

## API Overview

### Data Operations
```python
# Store any data
ref = slay.put(data)

# Retrieve data (with local caching)
data = ref.get()
data = slay.get(ref)

# Retrieve multiple references in parallel
results = slay.get_many([ref1, ref2, ref3])
```

### Function Execution
```python
# Store function
fn = slay.put(my_function)

# Execute it - core operation
result_ref = slay.run(fn, arg1, arg2)

# Or use syntactic sugar (same as above)
result_ref = fn(arg1, arg2)

# Get result
result = result_ref.get()
```

### Parallel Execution
```python
# Apply function to multiple items
fn = slay.put(my_function)
result_refs = slay.apply(fn, items)
results = slay.get_many(result_refs)
```

### Function Composition
```python
# Store function
fn = slay.put(my_function)

# Wrap with behaviors (returns callable reference)
cached_fn = slay.add_caching(fn)
resilient_fn = slay.add_retry(cached_fn, attempts=5)

# Execute composed function
result_ref = resilient_fn(arg)
result = result_ref.get()
```

## Reference Properties

### Callable References
Any reference containing a function can be called directly:
```python
fn = slay.put(lambda x: x ** 2)
result_ref = fn(5)  # Calls run(fn, 5)
```

### Reference Arguments
A Reference given as a top-level argument goes to the backend as it is.
This holds for positional and keyword arguments of `run()` and `fn(...)`,
and for the items of `apply()`. The backend fetches the value and calls
the function with it, so results chain without a round trip to the client:
```python
add_one = slay.put(lambda x: x + 1)
double = slay.put(lambda x: x * 2)
twelve = double(add_one(5))  # 6 never reaches the client
assert twelve.get() == 12
```
A Reference inside a list, dict or other container is not resolved. The
function receives the Reference object and can call `.get()` itself.

### Content-Addressed Equality
References with identical content are equal:
```python
ref1 = slay.put("data")
ref2 = slay.put("data")
assert ref1 == ref2  # True!
```

### Local Caching
`ref.get()` caches results locally after first fetch:
```python
ref = slay.put(large_dataset)
data1 = ref.get()  # Fetches from storage
data2 = ref.get()  # Returns cached locally
```

## Implementation Details

### Reference.__call__()
When you call a reference like `fn(arg)`, it:
1. Delegates to `slay.run(fn, arg)`
2. Which creates references for the arguments that are not references already
3. Which calls `backend.execute()`
4. Which returns a reference to the result

### Backwards Compatibility
The old API (`run_remotely`, `run_parallel`) is completely removed in v2.0:
- Breaking change but much cleaner
- Users migrate easily: `run_remotely(fn, x)` → `slay.run(fn, x)` or `fn(x)`

## Design Principles

1. **Single Responsibility**: Each function does one thing well
2. **Composability**: References can be wrapped, passed, and composed
3. **Consistency**: `put/get`, `put/run`, `get_many/apply` are mirrors
4. **Pythonic**: Callable syntax feels natural to Python developers
5. **Explicit**: Core operations have clear names
6. **Convenient**: Syntactic sugar for common patterns

## Migration from v1.0

| Old API | New API (Explicit) | New API (Sugar) |
|---------|-------------------|-----------------|
| `run_remotely(fn, x)` | `slay.run(fn, x)` | `fn(x)` |
| `run_parallel(fn, items)` | `slay.apply(fn, items)` | N/A |
| `create_reference(fn)` | `slay.put(fn)` | N/A |
| Getting result via `.data` | `ref.get()` or `slay.get(ref)` | N/A |

## Future Enhancements

The architecture supports future additions:
- Async execution: `await fn(arg)`
- Streaming: `fn.stream(items)` returns async iterator
- Job tracking: `job = fn.spawn(arg); job.status(); job.result()`
- Custom executors: Pluggable execution strategies

All without changing the core `put/get/run/apply` primitives.
