# slay - Distributed Computing Made Simple

Transform any Python function into a scalable, resilient, cached service with one line of code.

## Quick Start

```python
import slay

# Store data
ref = slay.put("some data")
data = slay.get(ref)

# Store and execute a function
fn = slay.put(lambda x: x + 1)
result_ref = fn(5)
result = result_ref.get()  # Returns 6

# Parallel execution
fn = slay.put(lambda x: x * 2)
result_refs = slay.apply(fn, [1, 2, 3, 4, 5])
results = slay.get_many(result_refs)  # Returns [2, 4, 6, 8, 10]

# Add resilience
cached_fn = slay.add_caching(fn)
resilient_fn = slay.add_retry(cached_fn, attempts=5)
result_ref = resilient_fn(data)
```

## Key Features

### 1. Simple, Intuitive API
Put data/functions, call them, get results:
```python
ref = slay.put(data)  # Store data
data = slay.get(ref)  # Retrieve it

fn = slay.put(lambda x: x + 1)  # Store function
result_ref = fn(5)                # Execute it
result = result_ref.get()         # Get result
```

### 2. Parallel Processing at Scale
Execute functions on thousands of items instantly:
```python
fn = slay.put(lambda x: x ** 2)
result_refs = slay.apply(fn, range(10000))
results = slay.get_many(result_refs)
```

### 3. Function Composition
Add behaviors without changing your code:
```python
fn = slay.put(expensive_function)
cached = slay.add_caching(fn)
resilient = slay.add_retry(cached, attempts=5)

result_ref = resilient(arg)
result = result_ref.get()
```

### 4. Content-Addressed References
Everything is a reference - data, functions, results:
```python
ref1 = slay.put("data")
ref2 = slay.put("data")
assert ref1 == ref2  # Same content = equal references

# Automatic deduplication
# Same data = same hash = stored once
```

### 5. Zero Infrastructure
Deploy to AWS with no configuration:
```bash
cd infrastructure
cdk deploy
```
- S3 bucket (auto-created)
- Lambda functions (auto-deployed)
- Step Functions state machines for retries and fan-out (auto-deployed)
- DynamoDB tables (auto-created)

## Installation

### Development (Local Testing)
```bash
pip install -e ".[dev]"

# Run tests
pytest tests/

# Run examples
python examples/01_hello_world.py
python examples/02_parallel_processing.py
python examples/03_retry_caching.py
```

### Production (AWS Deployment)
```bash
# Install with AWS CDK support
pip install -e ".[cdk]"

# Deploy infrastructure from the clients' Python: the Lambda runs its version
cd infrastructure
pip install -r requirements.txt
cdk deploy

# Now you can use Lambda backend
python my_script.py
```

## Architecture

### Core Components

1. **Reference System** (`core/reference.py`)
   - Content-addressed references (SHA256)
   - Automatic tier selection
   - Location-independent data access

2. **Serialization** (`core/serializer.py`)
   - Dill-based function serialization
   - Format detection (JSON, Parquet, NumPy, dill)
   - Type preservation across boundaries

3. **Storage** (`core/storage.py`)
   - Multi-tier storage (inline < 1KB, S3 >= 1KB)
   - Automatic tier selection
   - S3 integration for cloud deployment

4. **Execution Backends** (`backends/`)
   - Local backend (development/testing)
   - Lambda backend (AWS cloud)
   - Fargate backend (coming soon)

5. **Public API** (`api.py`)
   - `put()` / `get()` / `get_many()` - store and fetch data and functions
   - `run()` - execute a function reference; `fn(x)` is the same call
   - `apply()` - run a function reference on many items in parallel
   - `start_apply()` / `attach()` - the same as a job that outlives the
     client

6. **Function Composition** (`wrappers.py`)
   - `add_retry()` - retry with backoff; on Lambda, a Step Functions loop
     that can also retry timeouts and crashes
   - `add_caching()` - a result cache in the storage bucket, shared by
     every client and Lambda
   - `add_cache_only()` - read that cache; a miss raises `CacheMiss`
   - More coming: rate limiting, concurrency control, etc.

## Usage Examples

### Example 1: Store and Retrieve Data
```python
import slay

# Store any data
ref = slay.put("hello world")
data = slay.get(ref)
print(data)  # "hello world"

# Content-addressed - same data = same reference
ref1 = slay.put("data")
ref2 = slay.put("data")
assert ref1 == ref2  # True!
```

### Example 2: Execute Functions Remotely
```python
import slay

fn = slay.put(lambda x: x ** 2)
result_ref = fn(5)
result = result_ref.get()
print(result)  # 25
```

### Example 3: Parallel Processing
```python
import slay

fn = slay.put(lambda x: x * 2)
result_refs = slay.apply(fn, [1, 2, 3, 4, 5])
results = slay.get_many(result_refs)
print(results)  # [2, 4, 6, 8, 10]
```

### Example 4: Function Composition
```python
import slay

def fetch_api_data(url):
    return {"data": "from_api"}

fn = slay.put(fetch_api_data)
cached_fn = slay.add_caching(fn)
resilient_fn = slay.add_retry(cached_fn, attempts=5, backoff=2.0)

result_ref = resilient_fn("https://api.example.com/data")
result = result_ref.get()
```

### Example 5: Chain Results Without Downloading Them
```python
import slay

add_one = slay.put(lambda x: x + 1)
double = slay.put(lambda x: x * 2)

six = add_one(5)        # Reference to 6; nothing is downloaded
twelve = double(six)    # six goes to the backend as a reference
print(twelve.get())     # 12
```

A Reference given as a top-level argument, positional or keyword, goes to
the backend as it is. The backend fetches its value and calls the function
with that value. `apply()` treats Reference items the same way. A Reference
inside a list or dict is not resolved: the function gets the Reference object.

## Errors

A function that raises on the backend raises the same exception type in the
caller, so `except ValueError` works as it does for a local call:

```python
import slay

fn = slay.put(lambda text: int(text))
try:
    fn("not a number")
except ValueError as e:
    remote = e.__cause__              # slay.RemoteExecutionError on Lambda
    print(remote.remote_traceback)    # the traceback text from the Lambda
    print(remote.remote_logs)         # stdout and stderr of the call
```

- **Lambda backend.** The exception's `__cause__` is a
  `RemoteExecutionError`. It holds the remote traceback
  (`remote_traceback`), the captured output (`remote_logs`) and the
  exception's type name (`error_type`). The exception travels as a dill
  value, which keeps its arguments and attributes. dill does not keep
  `__traceback__`, `__cause__` or `__context__`, so the remote traceback
  text is the only record of the remote chain.
- **Custom exceptions.** dill rebuilds an exception as pickle does: it
  calls the class with the exception's `args`, then restores the
  attributes. If `__init__` takes parameters other than the `args` it
  passes to `Exception.__init__`, give the class a `__reduce__`. Without
  one, the message can change, or the load fails and the caller gets
  `RemoteExecutionError`.
- **Local backend.** The exception propagates unchanged, with its own
  traceback.
- **slay errors.** An error that slay raises on the backend keeps its type
  as well. A function written by another Python minor version raises
  `slay.exceptions.SerializationError`, on Lambda as locally.
- **`apply()`.** It raises the exception of the lowest failing item index.
  The Lambda backend waits for every item first. The local backend stops at
  the first failure, so later items do not run.
- **The exception cannot cross the wire.** If the Lambda cannot serialize
  the exception, or the client cannot load it, the caller gets
  `RemoteExecutionError` itself. Its `error_type` names the original type.
- **The Lambda fails outside the function.** On a timeout, an
  out-of-memory kill or a runtime crash, the caller gets
  `RemoteExecutionError`. Its `error_type` is the Lambda `errorType` (for
  example `Sandbox.Timedout`), and its `message` is the Lambda
  `errorMessage`.
- **The invocation fails.** Throttling, a missing function or missing
  credentials raise `RemoteExecutionError`, chained from the boto3 error.
- **Size.** The handler keeps the last 64 KB of the captured output, of
  the traceback text and of the message (`message`). A Step Functions
  state holds at most 256 KB, and a retried call passes the handler's
  response through one. The exception itself travels by reference, so it
  keeps its full message.

## Caching

`add_caching(fn)` returns a function Reference that stores each result in
the storage bucket, keyed by the call. Every client and every Lambda that
uses the same `SLAY_STORAGE_BUCKET` shares the entries.

```python
import slay

square = slay.put(lambda x: x * x)
cached = slay.add_caching(square, version="1")
cached(12).get()        # runs square on the backend, then writes the entry
cached(12).get()        # reads the entry; no backend call

strict = slay.add_cache_only(square, version="1")
strict(12).get()        # reads the same entry
strict(13)              # raises slay.CacheMiss; nothing runs
```

- **Key.** The key is a SHA-256 hash of the function's hash, the hashes
  of the argument References (keyword arguments by name) and `version`.
  The arguments are References already, so the key costs no
  serialization and no upload. `f(5)` and `f(x=5)` have different keys.
- **Entry.** An entry is the result Reference as wire JSON, at
  `cache/<k[:2]>/<k>.json` in `SLAY_STORAGE_BUCKET` (an S3 bucket or a
  directory). The cache needs the variable even for small results.
- **The wrapper.** `add_caching` stores its wrapper like any other value.
  The wrapper is about 400 bytes larger than the function, so wrapping a
  function of about 600 bytes or more needs `SLAY_STORAGE_BUCKET` too.
- **Lookup.** `run()` reads the entry before it calls the backend, so a
  hit makes no Lambda call. On a miss, `run()` calls the function through
  the backend and then writes the entry. A call that raises writes no
  entry.
- **`apply()`.** It looks up every item first. Then it runs each distinct
  missing call once, in one fan-out, and returns the results in item
  order.
- **Cache-only.** `add_cache_only(fn, version=None)` reads the same
  entries. A miss raises `slay.CacheMiss` and runs nothing. In `apply()`,
  the error names the lowest missing item index.
- **Expiry.** The bucket's lifecycle rule deletes every object after 90
  days, entries included. On a hit whose result is stored in S3, slay
  checks that the result object still exists. A missing object counts as
  a miss.
- **Permissions.** Clients need `s3:ListBucket` on the bucket. Without
  it, S3 reports a missing object as access denied, and slay raises
  `StorageError` instead of counting a miss.
- **Versions.** dill stores a lambda, or a function defined in `__main__`
  or inside another function, by value, so a change to its code changes
  its hash and its keys. dill stores a function defined at the top level
  of an importable module by reference to its name. Its hash stays the
  same when its code changes, so change `version` when you change it.
  Change `version` too when the function's result changes for another
  reason, for example new external data.
- **Unstable keys.** dill's bytes for a set of strings depend on
  `PYTHONHASHSEED`, so such an argument can hash differently in another
  process. The call then misses; it never returns a wrong result.
- **`ttl`.** `add_caching` accepts a `ttl` argument and ignores it.
- **Wrapped functions.** For `add_caching(add_retry(fn))`, the key uses
  the hash of the retry wrapper, so new retry settings start new entries.

## Retry

`add_retry(fn)` returns a function Reference that calls `fn` again when an
attempt fails:

```python
import slay

fetch = slay.put(fetch_page)
resilient = slay.add_retry(fetch, attempts=5, backoff=2.0,
                           retry_on_timeout=True, timeout=30)
page = resilient("https://example.com").get()
```

- **Attempts and waits.** `attempts` counts every attempt, the first
  included. Before attempt n + 1, slay waits `backoff * 2 ** (n - 1)`
  seconds: 2, 4, 8 and 16 seconds in the example.
- **Error classes.** An attempt fails in one of three ways:
  - *failure*: the function raised. `retry_on_failure` (default `True`)
    retries it.
  - *timeout*: the attempt ran longer than `timeout` seconds, or the
    Lambda reached its own timeout. `retry_on_timeout` (default `False`)
    retries it.
  - *crash*: the Lambda failed outside the function, for example out of
    memory. `retry_on_crash` (default `False`) retries it.
- **When retries end.** If the last attempt raised, the caller gets that
  exception, as a call without retry does (see [Errors](#errors)). If the
  last attempt timed out or crashed, the caller gets
  `RemoteExecutionError`. Its `error_type` is the Step Functions error
  name, for example `States.Timeout`, or the Lambda `errorType`.
- **Lambda backend.** A retried call runs as an execution of the
  `slay-retry` state machine (Step Functions, Standard). The client starts
  the execution and polls it until it ends. Step Functions waits in whole
  seconds, so slay rounds each wait up. In `apply()`, every item of the
  `slay-fanout` execution runs the same loop (see [Fan-out](#fan-out)).
- **`timeout`.** A per-attempt limit in whole seconds. Without it, the
  Lambda's own 14-minute timeout applies. A Step Functions timeout stops
  the wait for the attempt, not the Lambda: the Lambda runs on until it
  returns or reaches its own timeout.
- **Local backend.** The retry loop runs in the process. It cannot
  interrupt an attempt, so it ignores `timeout` and retries only
  failures.
- **Composition.** `add_caching(add_retry(fn))` caches the result of the
  retried call. `add_retry(add_caching(fn))` retries a call that reads and
  writes the cache inside the executor.
- **Arguments.** `attempts` must be at least 1, `backoff` must not be
  negative, and `timeout` must be a positive whole number or `None`.
  `add_retry` raises `ValueError` otherwise.
- **Plain calls.** `run()` without `add_retry` calls the Lambda directly,
  as before.

## Fan-out

`apply(fn, items, max_concurrent=None)` calls `fn` once per item and
returns the result References in item order:

```python
import slay

square = slay.put(lambda x: x * x)
refs = slay.apply(square, range(10_000), max_concurrent=100)
squares = slay.get_many(refs)
```

- **Lambda backend.** `apply()` runs one execution of the `slay-fanout`
  state machine (Step Functions, Standard). The client writes the calls
  to `fanout/<execution name>/items.json` in the storage bucket. A
  Distributed Map reads that file and starts one Standard child execution
  per item. Each child runs the loop of [Retry](#retry), which makes one
  attempt unless the function comes from `add_retry`.
- **Client I/O.** Your link to AWS carries the values you store (each
  once), one items file of References, the start request, the status
  polls and the result files, which also hold References. A value
  crosses it again only when you call `.get()`.
- **I/O inside AWS.** Each item's invoke, argument fetch and result write
  runs between Step Functions, Lambda and S3. The executor fetches the
  arguments before your function starts, so your function does no I/O
  for its arguments, however many items there are.
- **Results.** A `ResultWriter` writes the child outputs under
  `fanout/results/` in the bucket, and the client reads them when the
  execution ends. Each output carries its item index, because the result
  files are in no particular order.
- **`max_concurrent`.** The most children that run at once. `None` lets
  Step Functions decide. The local backend runs the items one at a time
  and ignores it.
- **The bucket.** `SLAY_STORAGE_BUCKET` must name the stack's S3 bucket:
  the state machine reads the calls from it and writes the results to it.
  A directory raises `StorageError` before anything starts. The bucket's
  lifecycle rule deletes these files after 90 days.
- **Errors.** Every item runs to completion. `apply()` then raises the
  error of the lowest failing index (see [Errors](#errors)).
- **Plain calls.** `run()` without `add_retry` still invokes the Lambda
  directly.

### Jobs

`apply()` waits for its results. `start_apply()` starts the same work and
returns a `Job` at once:

```python
job = slay.start_apply(square, range(10_000))
job_id = job.id                # on Lambda: the execution ARN
# The client may exit now; the execution goes on.
job = slay.attach(job_id)      # later, in any client of the same account
refs = job.results()           # waits, then returns or raises as apply() does
```

- **Status.** `job.status()` returns `"RUNNING"`, `"SUCCEEDED"`,
  `"FAILED"`, `"TIMED_OUT"` or `"ABORTED"`. A job whose items raised has
  the status `"SUCCEEDED"`, because its execution finished;
  `job.results()` raises the error.
- **Wrappers.** `start_apply()` hands the whole call to the backend at
  once. For a function from `add_retry`, the retry settings go into the
  execution. Any other wrapper goes to each executor as the function: an
  `add_caching` function looks up each item inside its executor, and
  duplicate items are not merged. `apply()` looks up the cache in the
  client instead, and runs each distinct miss once.
- **Local backend.** `start_apply()` runs the items before it returns,
  so `job.status()` is `"SUCCEEDED"`. The job lives in the memory of the
  process, so `attach()` finds it only in that process. For an unknown
  ID, `job.status()` and `job.results()` raise `ValueError`.

### Nested calls

A function that runs on Lambda can call `run()` or `apply()` itself.
The executor sets `SLAY_BACKEND=lambda`, so nested calls go
to AWS: the executor may invoke itself and start executions of both state
machines. Nothing stops a function that calls itself without end, and
each level is a billed Lambda invocation.

dill stores a module that is outside the standard library and
site-packages by value. On Lambda, slay itself is such a module. slay
therefore makes dill store its own modules by name, so a function that
names the `slay` module serializes in the client and in the executor.

## Permissions

A client that uses the Lambda backend needs:

- `lambda:InvokeFunction` on `slay-executor`, and `lambda:GetFunction`
  on it for `SLAY_BACKEND=auto`: without it, auto-detection picks the
  local backend;
- `s3:GetObject`, `s3:PutObject` and `s3:ListBucket` on the storage
  bucket (see [Caching](#caching) for why `ListBucket`);
- for `add_retry`: `states:StartExecution` on the `slay-retry` state
  machine and `states:DescribeExecution` on its executions;
- for `apply()`, `start_apply()` and `attach()`: the same on the
  `slay-fanout` state machine and its executions.

slay builds the state machine ARNs from the account that
`sts:GetCallerIdentity` returns. That call needs no permission.

`IAM_POLICIES/SlayClient.json` grants these rights, and `IAM_POLICY.json`
holds them together with the deployment rights. To give them to an IAM
group (see `IAM_POLICIES/README.md`):

```bash
cd IAM_POLICIES
./manage-policies.sh apply -t slayers            # deploy and use slay
./manage-policies.sh apply -t slay-users --client  # use slay only
```

## Deployment

### Local Development
```bash
# No setup needed!
python examples/01_hello_world.py
```

### AWS Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for detailed setup instructions.

**Quick start:**
```bash
# 1. Configure AWS credentials
export CDK_DEFAULT_ACCOUNT="123456789012"  # Replace with your account ID
export CDK_DEFAULT_REGION="us-east-1"

# 2. Deploy AWS resources
cd infrastructure
pip install -r requirements.txt
cdk deploy

# 3. Point clients at the bucket (the StorageBucket output of cdk deploy)
export SLAY_STORAGE_BUCKET="slay-storage-123456789012-us-east-1"

# 4. Set backend in your code
import slay
slay.set_backend('lambda')

# 5. Run your code - it now executes on Lambda
fn = slay.put(my_function)
result = fn(data).get()
```

**Getting your AWS Account ID:**
```bash
aws sts get-caller-identity
```

For complete troubleshooting and security best practices, see [DEPLOYMENT.md](DEPLOYMENT.md).

## Project Structure

```
c/
├── src/slay/                  # Main package
│   ├── core/                  # Core abstractions
│   │   ├── reference.py       # Content-addressed references
│   │   ├── serializer.py      # Serialization system
│   │   ├── storage.py         # Storage tiers
│   │   └── context.py         # Execution context
│   ├── backends/              # Execution backends
│   │   ├── base.py            # Abstract backend
│   │   ├── local.py           # Local execution
│   │   └── lambda_.py         # AWS Lambda
│   ├── api.py                 # Public API
│   ├── jobs.py                # Job (start_apply, attach)
│   ├── wrappers.py            # Function composition
│   └── exceptions.py          # Exception types
├── infrastructure/            # AWS CDK infrastructure
│   ├── app.py                 # CDK app
│   ├── stacks/                # CDK stacks
│   │   ├── storage.py         # S3 + DynamoDB
│   │   ├── compute.py         # Lambda function and state machines
│   │   ├── retry_loop.py      # The retry loop (slay-retry)
│   │   └── fanout.py          # The Distributed Map (slay-fanout)
│   └── cdk.json               # CDK config
├── runtime/                   # Lambda runtime
│   ├── handler.py             # Lambda entry point
│   └── requirements.txt
├── tests/                     # Test suite
│   ├── unit/                  # Unit tests
│   └── infrastructure/        # CDK stacks; state machines in moto
├── examples/                  # Usage examples
└── README.md
```

## Configuration

### Environment Variables

- `SLAY_BACKEND` - Backend to use: `auto` (default), `local`, `lambda`
- `SLAY_STORAGE_BUCKET` - Where values of 1 KB or more go: the S3 bucket
  name (the `StorageBucket` output of `SlayStorageStack`), or an existing
  local directory. Smaller values travel inside the reference and need no
  storage. CDK sets the variable inside the Lambda only; set it on clients
  yourself. Without it, storing a large value raises `StorageError`.

### Python Config

```python
import slay

# Set backend
slay.set_backend('lambda')

# Or configure globally
from slay.core.context import set_config
set_config('max_workers', 100)
```

## Testing

### Run All Tests
```bash
pytest tests/
```

### Run Specific Test File
```bash
pytest tests/unit/test_reference.py -v
```

### Run with Coverage
```bash
pytest tests/ --cov=slay --cov-report=html
```

## Limitations (MVP)

- **Execution time**: Limited to 14 minutes (Lambda limit: 15 min)
  - Use Fargate backend (Phase 2) for longer tasks
- **Memory**: Max 3GB (Lambda limit)
- **Parallelism**: Unlimited up to AWS account concurrency limits (~1000 Lambdas)
- **Python version**: Functions and other dill values load only under the
  Python minor version that wrote them. The Lambda runtime matches the
  Python that runs `cdk deploy`, so deploy with the client's Python.
- **Kill switch and throttling (`slay.control`)**: Not wired in. Nothing
  registers executions or checks for kills, so `kill_all()` and the other
  functions log a message and stop nothing. To stop runaway Lambda work,
  set the function's reserved concurrency to 0 in AWS.

## Performance Characteristics

### Local Backend
- Latency: < 1ms (in-process)
- Throughput: Sequential (for dev/testing)
- Max execution time: Unlimited

### Lambda Backend
- Cold start: 100ms - 1s
- Warm invocation: 100-500ms
- Max execution time: 15 minutes
- Throughput: 1000+ concurrent (account dependent)

## Costs (AWS)

**Example: Process 10,000 items**
- Each task: 512MB, 5 seconds
- Compute: 10,000 × 0.5GB × 5s × $0.0000166667 = **$0.42**
- Orchestration: 10,000 × about 4 state transitions × $0.000025 = **$1.00**
  (Step Functions, Standard)
- Storage: Variable (depends on data size)
- **Total: ~$1.42 for 10,000 executions**

## API Reference

### Core Functions

**`put(data_or_func) -> Reference`**
Store data or function, get a reference.

**`get(ref) -> Any`**
Fetch data from a reference.

**`get_many(refs: list[Reference]) -> list[Any]`**
Fetch multiple references in parallel.

**`run(fn: Reference, *args, **kwargs) -> Reference`**
Execute a function reference, returns a reference to the result.
Reference arguments pass through without a download.

**`apply(fn: Reference, items: list[Any], max_concurrent=None) -> list[Reference]`**
Apply function to multiple items, returns references to results.
Reference items pass through without a download. See [Fan-out](#fan-out).

**`start_apply(fn: Reference, items: list[Any], max_concurrent=None) -> Job`**
Start the work of `apply()` and return at once (see [Jobs](#jobs)).

**`attach(job_id: str) -> Job`**
The job with this ID. **`Job.id`**, **`Job.status()`** and
**`Job.results()`** report on it.

### Reference Methods

**`ref.get() -> Any`**
Fetch data from reference (with local caching).

**`fn(arg1, arg2, ...) -> Reference`**
Execute function reference remotely, returns reference to result.
Same as `run(fn, arg1, arg2, ...)`.

**`ref1 == ref2`**
Compare by content hash (content-addressed equality).

### Wrappers

**`add_retry(fn, attempts=3, backoff=1.0, retry_on_failure=True,
retry_on_timeout=False, retry_on_crash=False, timeout=None) -> Reference`**
Call the function again when an attempt fails (see [Retry](#retry)).

**`add_caching(fn, ttl=None, version=None) -> Reference`**
Cache results in the storage bucket, shared by every client and Lambda
(see [Caching](#caching)). `ttl` is accepted and ignored.

**`add_cache_only(fn, version=None) -> Reference`**
Read the entries that `add_caching` writes; a miss raises `slay.CacheMiss`.

### Exceptions

A failed call raises the function's own exception (see [Errors](#errors)).

**`slay.RemoteExecutionError`**
The backend's report of a failed call: the `__cause__` of a re-raised
exception on Lambda, or the error itself when the exception cannot cross
the wire or the Lambda fails outside the function. Attributes: `message`,
`remote_traceback`, `remote_logs`, `error_type`.

**`slay.CacheMiss`**
Raised by a function from `add_cache_only` when no entry exists.

**`slay.SlayError`**
Base class of every slay error, including `RemoteExecutionError`,
`CacheMiss`, `slay.exceptions.SerializationError` and
`slay.exceptions.StorageError`.

### Configuration

**`slay.set_backend(name: str)`**
Set execution backend: `'local'` (default) or `'lambda'`.

**`slay.get_backend()`**
Get current backend.

## Contributing

This is an MVP implementation. Key areas for contribution:

1. **Additional Wrappers**: Rate limiting, concurrency control
2. **Additional Backends**: Fargate, EC2, Kubernetes, Ray
3. **Orchestration**: Job tracking, durable execution, Step Functions
4. **Observability**: CloudWatch dashboards, X-Ray tracing
5. **Performance**: Parallel storage.load_many(), network optimizations
6. **Testing**: Integration tests, e2e tests, chaos engineering

## License

MIT
