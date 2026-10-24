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

# At most 100 calls at once
result_refs = slay.apply(fn, items, max_concurrent=100)

# The same work as a job that outlives the client
job = slay.start_apply(fn, items)
result_refs = slay.attach(job.id).results()
```
On Lambda, `apply()` and `start_apply()` run one Step Functions execution
with a Distributed Map. The README sections "Fan-out" and "Jobs" have the
details.

### Deferred and Scheduled Calls
```python
# One call that starts now, or at a time; the client need not wait
job = slay.run_later(fn, arg)
job = slay.run_at(datetime.timedelta(hours=1), fn, arg)
result_ref = slay.attach(job.id).result()

# The same call again and again, on an EventBridge Scheduler expression
slay.schedule("nightly", "cron(0 2 * * ? *)", fn, arg)
slay.unschedule("nightly")
```
On Lambda, each call or run is one execution of the retry state machine.
A last attempt that fails sends a dead letter to an SQS queue. The README
section "Later, at a time, and on a schedule" has the details.

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
`add_retry` calls the function again when an attempt fails. On Lambda, a
Step Functions loop runs the attempts, so it can also retry a timeout or
a crash. `add_caching` stores results in the storage bucket, where every
client and Lambda shares them. A hit makes no backend call. `add_cache_only` reads
the same entries and raises `CacheMiss` on a miss. The README section
"Caching" has the details.

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

### Errors
A remote call raises the function's own exception type, as a local call
does. On Lambda, the exception's `__cause__` is a `RemoteExecutionError`
with the remote traceback, the captured output and the type name:
```python
fn = slay.put(lambda text: int(text))
try:
    fn("not a number")
except ValueError as e:
    print(e.__cause__.remote_traceback)
```
`apply()` raises the exception of the lowest failing item index. The caller
gets `RemoteExecutionError` itself only when the exception cannot cross the
wire, or when the Lambda fails outside the function. The README section
"Errors" lists every case.

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
- Custom executors: Pluggable execution strategies

All without changing the core `put/get/run/apply` primitives.
