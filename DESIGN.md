# Slay Design Document

## Overview

Slay is a distributed computing platform that makes remote code execution as simple as calling a local function. The design emphasizes:

1. **Simplicity**: Write normal Python code, add one keyword, scale to thousands of executions
2. **Zero Infrastructure**: No YAML, containers, or configuration needed
3. **Content Addressing**: Automatic deduplication via SHA256 hashing
4. **Composability**: Layer behaviors (retry, caching, rate limiting) without modifying code
5. **Multi-Tier Storage**: Automatic selection of optimal storage tier

## Architecture

### Core Components

#### 1. Reference System (`core/reference.py`)

Everything in slay becomes a **Reference** - an immutable, content-addressed identifier.

```
Reference {
  hash: "abc123..."           # SHA256 of content
  size: 1024                  # Bytes
  storage_tier: "inline"      # inline, s3, cache
  type_info: TypeInfo         # Type metadata
  inline_data: bytes | None   # For small data
  s3_key: str | None          # For S3 data
}
```

**Key Properties:**
- Deterministic: Same data = same hash = same reference
- Location-independent: Can fetch from any storage
- Lazy-loading: Data fetched only when accessed via `.data` property
- Immutable: References never change

**Usage:**
```python
ref = create_reference(my_function)     # Returns Reference
result = ref.data                        # Lazy-load the actual object
```

#### 2. Serialization System (`core/serializer.py`)

Dill-based serialization with automatic format detection.

**Supported Formats:**
- **JSON**: Values that JSON returns unchanged (see below)
- **Parquet**: pandas DataFrames (optimized)
- **NumPy**: numpy arrays (binary)
- **Dill**: Functions, classes, custom objects (fallback)

**Format Selection Algorithm:**
1. JSON, if the value round-trips exactly
2. Check for pandas DataFrame → Parquet
3. Check for numpy array → NPY
4. Fallback to dill (handles everything)

A value round-trips exactly through JSON when it contains only these exact
types: `None`, `bool`, `int`, `float`, `str`, `list`, and `dict` with `str`
keys. Subclasses do not qualify. A tuple, a set, or a dict with a non-`str`
key goes to dill, so `put((1, 2)).get()` returns a tuple. Because the bytes
differ, `put((1, 2))` and `put([1, 2])` give different references.

**Why Dill over Cloudpickle:**
- Better at serializing non-standard Python objects
- More flexible for diverse use cases
- Similar capabilities for function closure handling
- Can be swapped later if needed

**Type Preservation:**
```python
@dataclass
class TypeInfo:
  format: str        # "json", "parquet", "dill"
  type_name: str     # "dict", "DataFrame", "function"
  module: str        # "__main__", "pandas.core.frame"
  metadata: dict     # Format-specific metadata
```

**Python versions:** dill stores bytecode, and bytecode changes between
Python minor versions. For a dill value, `metadata["python"]` records the
writer's version, for example `"3.13"`. Reading the value under another
minor version raises `SerializationError`, and the message names both
versions. A dill value without this field (written before the field existed)
loads as before. JSON, Parquet and NumPy values do not depend on the Python
version and carry no such field.

**slay's own modules:** dill stores a module by value when its file is
outside the standard library and site-packages, and slay's module state
(a thread-local, for one) does not pickle. In the Lambda, slay sits in
`/var/task`; in a development checkout, in `src/`. `serialize` therefore
uses a dill Pickler that stores `slay` and its submodules by name, so a
function that names the `slay` module serializes there too. Loading it
imports slay, which every executor has.

#### 3. Multi-Tier Storage System (`core/storage.py`)

Automatic tier selection based on data size and context.

**Tier Selection:**
```
Data Size        Tier        Storage Medium
-----------      ----        ---------------
< 1KB           inline       Embedded in reference
1KB - 4MB       S3           Amazon S3 (or local FS)
> 4MB           S3           Amazon S3 (or local FS)
```

**Key Design:**
- `SLAY_STORAGE_BUCKET` names the store for values of 1 KB or more. An
  existing directory selects the local filesystem; any other value is an S3
  bucket name. There is no default and no discovery: without the variable,
  storing such a value raises `StorageError`, which names the variable
- S3 operations are abstracted and can fall back to local filesystem (for testing)
- All data is sharded by first 2 chars of hash for S3 performance
- Keys: `refs/<h[:2]>/<h>.bin` for values, `cache/<k[:2]>/<k>.json` for
  result cache entries (see Function Composition), `fanout/` for the files
  of a fan-out (section 11), and `schedules/<name>/<h>.bin` for the copies
  that a schedule keeps (section 12)
- Lifecycle: in the S3 bucket, `refs/`, `cache/` and `fanout/` move to
  Intelligent-Tiering after 30 days and expire after 90. No rule applies
  to `schedules/`
- `write_object`, `read_object` and `object_exists` work on any key, in S3
  or in the directory. `read_object` returns None for a missing key. S3
  reports a missing key as 404 only to a caller with `s3:ListBucket`;
  without that right, the 403 raises `StorageError`
- Metadata stored alongside data
- Content-addressed means automatic deduplication

**Tier Selection Logic:**
```python
def choose_tier(size: int) -> str:
  if size < 1024:      # 1KB
    return "inline"
  elif size < 4_000_000:  # 4MB
    return "s3"
  else:
    return "s3"
```

#### 4. Execution Context (`core/context.py`)

Global state management and backend selection.

**Features:**
- Auto-detect available backend (AWS → local)
- Explicit backend selection via `set_backend()`
- Configuration management
- Environment-based discovery

**Backend Auto-Detection:**
1. Check for AWS credentials (via boto3)
2. Try to invoke Lambda function
3. Fall back to local execution if unavailable

#### 5. Execution Backends

**Backend Interface (`backends/base.py`):**
```python
class ExecutorBackend(ABC):
  # args: list[Reference], kwargs: dict[str, Reference]
  # retry: a Retried policy (see Function Composition), or None
  # Raises the function's own exception (see Error Handling below)
  def execute(func_ref, args, kwargs, retry=None) -> Reference
  # args_list: list of (args, kwargs) pairs shaped as above
  # max_concurrent: the most calls at once, or None for the backend's own
  # Raises the error of the lowest failing index
  def execute_many(func_ref, args_list, retry=None, max_concurrent=None) -> List[Reference]

  # Jobs: execute_many in two steps, so a client can leave and come back
  def start_many(func_ref, args_list, retry=None, max_concurrent=None) -> str  # job ID
  def job_status(job_id) -> str     # "RUNNING", "SUCCEEDED", "FAILED", ...
  def job_results(job_id) -> List[Reference]  # waits; raises as execute_many

  # A job of one call (section 12). at: an aware UTC datetime, or None
  def start_one(func_ref, args, kwargs, retry=None, at=None) -> str  # job ID
  def job_result(job_id) -> Reference  # waits; raises as execute
  # job_results of a one-call job, and job_result of a job of many
  # calls, raise ValueError

  # Schedules (section 12); replace=False raises ValueError for a used name
  def schedule(name, expression, func_ref, args, kwargs, retry=None, replace=False) -> None
  def unschedule(name) -> None      # ValueError if no schedule has the name
  def schedule_names() -> List[str] # sorted
  
  @property
  def name() -> str               # "local", "lambda", etc
  
  @property
  def max_execution_time() -> int # Seconds (or None)
```

**Local Backend (`backends/local.py`):**
- Executes functions in current process
- No parallelism (sequential)
- Unlimited execution time
- Used for development/testing
- A function's exception propagates unchanged; `execute_many` stops at the
  first failure
- With `retry`, an in-process loop runs each call. It sleeps
  `backoff * 2 ** n` seconds between attempts and retries only failures:
  it cannot interrupt an attempt, so `timeout` does not apply
- `max_concurrent` does not apply. `start_many` runs the items before it
  returns and keeps the results, or the error, in a dict in memory under
  a new job ID. `job_status` of a known ID is `"SUCCEEDED"`; `job_status`
  and `job_results` of an unknown ID raise `ValueError`
- `start_one` sleeps until `at`, if it is in the future, then runs
  `execute(func_ref, args, kwargs, retry)` and keeps the result or the
  error under a new job ID. The dict records whether a job holds one call
  or many, so `job_result` and `job_results` can refuse the other kind
- `schedule`, `unschedule` and `schedule_names` raise `SlayError`: no
  process outlives a schedule

**Lambda Backend (`backends/lambda_.py`):**
- Invokes AWS Lambda function
- 15-minute timeout per execution
- 1000+ concurrent executions (account dependent)
- Re-raises the function's exception (see Error Handling below);
  `execute_many` waits for every item, then raises the error of the lowest
  failing index
- `execute`: without `retry`, invokes the Lambda directly. With `retry`,
  starts an execution of the `slay-retry` state machine (see section 10)
  and polls `DescribeExecution` until it ends
- `execute_many` is `job_results(start_many(...))`. `start_many` writes
  the calls to the bucket and starts an execution of `slay-fanout` (see
  section 11); the job ID is the execution ARN. `job_results` polls the
  execution, then reads the results the Map wrote
- `start_one` starts a `slay-retry` execution with a dead-letter flag,
  and with `at` if given, and does not poll it (section 12). The job ID is
  the execution ARN. `job_result` polls the execution, then decodes its
  output as `execute` does
- A job ID names its state machine: `slay-retry` for one call,
  `slay-fanout` for many. Any other ID raises `ValueError`
- `schedule`, `unschedule` and `schedule_names` work through
  `backends/schedules.py` (section 12)
- State machine ARNs come from the region of the Step Functions client
  and the account from `sts:GetCallerIdentity`, looked up once per
  backend. The ARN of the role `slay-scheduler` comes from the same
  account

**Design Notes:**
- Backends are swappable - easy to add Fargate, EC2, Kubernetes, etc.
- All backends implement same interface
- Serialization/deserialization happens in Lambda runtime, not client

#### 6. Public API (`api.py`)

Simple, intuitive user-facing interface.

```python
fn = slay.put(function)

# Remote execution returns a Reference; fn(...) is the same call
result_ref = slay.run(fn, arg1, arg2, kwarg=value)
result = result_ref.get()

# Parallel execution returns a list of References
results = slay.get_many(slay.apply(fn, items))
```

**Implementation:**
1. Create a reference for each argument that is not a Reference already
2. Get backend (auto-detected or explicit)
3. Dispatch with `slay.wrappers.call(func_ref, args_refs, kwargs_refs,
   backend)`: a wrapper runs its policy (see Function Composition), and a
   plain function goes to `backend.execute`
4. Return the result Reference; the caller fetches it with `.get()`

Top-level Reference arguments pass through to the backend unchanged, and the
backend resolves them. Results can therefore feed the next call without a
download. A Reference inside a container is not resolved.

#### 7. Function Composition (`wrappers.py`)

Layer behaviors without modifying original function.

**Current Wrappers:**
- `add_retry()`: Retry with exponential backoff; on Lambda, timeouts and
  crashes too
- `add_caching()`: A result cache in the storage bucket, shared by every
  client and Lambda
- `add_cache_only()`: Reads that cache; a miss raises `CacheMiss`

**Design Pattern:**
```python
base_ref = create_reference(my_func)
retried = add_retry(base_ref, attempts=5)
cached = add_caching(retried)

# retried and cached are NEW references, not modified versions
# my_func is unchanged
```

**Wrapper Implementation:**

Each wrapper returns a Reference to a small policy object, a frozen
dataclass in `slay.wrappers`. Building it does not fetch the inner
function.

- `Cached` (from `add_caching` and `add_cache_only`) holds the inner
  function's Reference, the `version`, and whether a miss is an error.
- `Retried` (from `add_retry`) holds the inner function's Reference,
  `attempts`, `backoff`, the three `retry_on_*` flags and `timeout`.
  `Retried.call` runs `backend.execute(inner, args, kwargs, retry=self)`,
  and `Retried.call_many` runs `backend.execute_many(..., retry=self)`.
  The backend decides how to retry.

1. The wrapper's Reference keeps the policy in its local cache
   (`_set_cached`), so the client never fetches it. The policy is stored
   like any other value: it pickles the inner Reference, about 400 bytes
   of overhead included, so a policy of 1 KB or more goes to the bucket.
2. `call(func_ref, args, kwargs, backend)` in `slay.wrappers` dispatches
   one call. It recognizes a wrapper by `type_info.module ==
   "slay.wrappers"`, without fetching it, and runs `policy.call(args,
   kwargs, backend)`. A plain function goes to `backend.execute`.
   `call_many` does the same for `apply()`, through `policy.call_many` or
   `backend.execute_many`.
3. `run()` and `apply()` dispatch with the current backend.
   `LocalBackend.execute` dispatches a wrapper with itself, and the Lambda
   handler runs every call through `LocalBackend`. A wrapper that reaches
   an executor therefore applies inside that executor.
4. Calling the policy object itself (`wrapper.get()(x)`) runs the call in
   the calling process, through `LocalBackend`.

When `inner` is itself a wrapper, the backend sends that wrapper to the
executor. In `add_retry(add_caching(fn))`, each attempt therefore reads
and writes the cache inside the executor.

**Shared Result Cache:**
```
Key:   sha256(JSON of {function: inner hash,
                       args: [argument hashes],
                       kwargs: {name: argument hash},
                       version: version})
Entry: cache/<k[:2]>/<k>.json = ref_to_dict(result Reference)
```

- The arguments are References before dispatch, so the key needs only
  their hashes: no serialization, no upload.
- A hit returns the stored Reference. If its tier is `s3`,
  `object_exists` checks the result object first, because the lifecycle
  rule deletes objects after 90 days. A missing object is a miss.
- On a miss, `Cached.call` runs `call(inner, ...)` with the same backend,
  then writes the entry. A call that raises writes no entry. Two clients
  that miss at the same time both run the call; the last write wins.
- `Cached.call_many` looks up all keys in parallel, runs the distinct
  missing keys through one `call_many(inner, ...)`, writes their entries,
  and returns the results in item order.
- Cache-only raises `CacheMiss` before anything runs; in `call_many`, for
  the lowest missing index.

#### 8. Lambda Runtime (`runtime/handler.py`)

Code that runs inside AWS Lambda.

**Flow:**
1. Receive event with serialized function and arguments
2. Fetch data from S3 via references
3. Deserialize using type information
4. Execute function (through `LocalBackend`, which applies wrappers)
5. Serialize result
6. Store in S3
7. Return result reference

The handler keeps the last 64 KB of the captured output, of the
traceback text and of `error` (the message). The response of a retried
call passes through a Step Functions state, which holds at most 256 KB.

**Error Handling:**

The goal is that `except` clauses work as they do for a local call. When
the function raises, the handler returns:

```
{
  status: "error",
  error: str(exception),
  error_type: type(exception).__name__,
  traceback: traceback text, including any chained exceptions,
  exception_ref: Reference to the exception (wire dict), or None,
  logs: captured stdout and stderr
}
```

- The handler stores the exception with `create_reference`. If that
  fails (dill cannot pickle an attribute, or storage fails),
  `exception_ref` is None.
- `LambdaBackend` loads the exception and raises it from a
  `RemoteExecutionError` that holds `error`, `error_type`, `traceback`
  and `logs`. The caller catches the original type and reads the remote
  details from `__cause__`.
- dill keeps the exception's arguments and attributes, but not
  `__traceback__`, `__cause__` or `__context__`. Setting `__cause__` on
  the client therefore loses nothing; the traceback text records the
  remote chain.
- dill rebuilds an exception as pickle does: it calls the class with
  `args`, then restores `__dict__`. An `__init__` whose parameters differ
  from `args` can change the message or fail the load, unless the class
  defines `__reduce__`. `RemoteExecutionError` defines one for this reason.
- If `exception_ref` is None, cannot be loaded, or does not hold an
  exception, the caller gets the `RemoteExecutionError` itself.
- slay errors raised in the handler (a `SerializationError` for a Python
  version mismatch, a `StorageError`) take the same path, so they reach the
  caller with their own type.
- `RemoteExecutionError` survives pickling with its message and
  attributes, so a nested call's error can cross the wire too.

When the Lambda fails outside the handler (timeout, out of memory, runtime
crash, import error), the invoke response carries `FunctionError`, and the
payload holds `errorType`, `errorMessage` and, for a Python error,
`stackTrace`. `LambdaBackend` checks `FunctionError` before it reads the
payload as a handler response. It raises a `RemoteExecutionError` whose
`error_type` is `errorType`, whose `message` is `errorMessage`, and whose
`remote_traceback` is the joined `stackTrace`.

**Packaging:** `cdk deploy` builds the Lambda code on the deploying machine,
without Docker. The bundle holds `runtime/handler.py`, a copy of `src/slay`,
and the packages in `runtime/requirements.txt`. pip installs them for the
Lambda platform and the runtime's Python version. The asset hash comes from
the bundle, so a change under `src/slay` produces a new deployment.

#### 9. AWS Infrastructure (`infrastructure/`)

CDK-based infrastructure deployment.

**Stacks:**
- **StorageStack**: S3 bucket + DynamoDB (optional)
- **ComputeStack**: Lambda function with IAM role, the `slay-retry` and
  `slay-fanout` state machines, the SQS queue `slay-dead-letter`, the
  EventBridge Scheduler group `slay`, and the role `slay-scheduler`

**Design:**
- Single Lambda function for all executions
- The Lambda Python runtime is the minor version of the Python that runs
  `cdk deploy`, because dill values load only under the version that wrote
  them. Deploying with a version that Lambda does not offer fails at synth.
- Stack outputs: `StorageBucket` (bucket name), `ExecutorFunction`
  (function name), `RetryStateMachine` and `FanoutStateMachine` (state
  machine names), `DeadLetterQueue` (queue name) and `ScheduleGroup`
  (schedule group name)
- Dead letters: `slay-dead-letter` is a standard SQS queue with SQS-managed
  encryption that keeps a message for 14 days. Only the `slay-retry` role
  may send to it (section 10)
- Schedules: the role `slay-scheduler` trusts `scheduler.amazonaws.com`
  for the stack's account (`aws:SourceAccount`). It may call
  `states:StartExecution` on `slay-retry` and nothing else. Clients pass
  it to Scheduler, so it has a fixed name, as the function does
- Nested calls: the executor's environment sets `SLAY_BACKEND=lambda`.
  Its role may call `lambda:InvokeFunction` on `slay-executor`, and
  `states:StartExecution` and `states:DescribeExecution` on the `slay-*`
  state machines and their executions. The policy names these ARNs from
  the fixed names, not from the resources, because the function, its role
  and the state machines would otherwise depend on each other
- S3 bucket for data storage
- Lifecycle rules by prefix: `refs/`, `cache/` and `fanout/` tier cold
  data and expire after 90 days; `schedules/` has no rule (section 12)
- CORS enabled for future web access

#### 10. Retry State Machine (`infrastructure/stacks/retry_loop.py`)

`slay-retry` is a Standard state machine with a fixed name. One helper,
`retry_loop(scope, executor, dead_letters=None)`, builds the loop, so
another state machine can reuse it (for example as a Map item processor).
`slay-retry` puts a wait in front of the loop and passes the dead-letter
queue; the fan-out's loop has neither.

**Input** (written by `LambdaBackend`, or by the fan-out's ItemSelector):
```
{
  index: 0,                                     # the item's index in a fan-out
  call: encode_call(func_ref, args, kwargs),
  retry: {
    waits: [seconds before attempt 2, 3, ...],  # whole seconds, rounded up
    retry_on: ["failure", "timeout", "crash"],  # a subset
    timeout: seconds per attempt,               # 900 when not set
    dead_letter: true                           # optional: report a failure
  },
  attempt: 0,
  at: "2026-10-24T12:00:00Z"                    # optional: when to start
}
```

`LambdaBackend.start_one` and the schedules set `retry.dead_letter`
(section 12), and `start_one` sets `at` for `run_at()`. A retried `run()`
sets neither. `at` is UTC in whole seconds, with a `Z`. The flag is part
of `retry` because `Next attempt` rebuilds the state from named fields,
and `retry` is one of them: a top-level flag would be lost after the
first retry.

The default `timeout` of 900 seconds exceeds the Lambda's 14-minute
timeout, so without a `timeout` the Lambda's own limit ends an attempt.

**States:**
0. In `slay-retry` only: `Delayed?`, a Choice on `$.at` being present,
   leads to `Wait until`, a `Wait` with `TimestampPath` = `$.at`, and then
   to `Attempt`. Without `at`, it leads to `Attempt` at once. A time in
   the past ends the wait at once. `Delayed?` adds one state transition
   to every `slay-retry` execution.
1. `Attempt`: a `lambda:invoke` Task with `Payload` = `$.call` and
   `TimeoutSecondsPath` = `$.retry.timeout`. Its Retry rules retry Lambda
   service errors and `Lambda.TooManyRequestsException`; these do not
   count as attempts. A Catch on `States.ALL` stores the error in
   `$.caught`.
2. `Raised?`: a Choice on `$.result.Payload.status == "error"`. A handler
   error has class `failure`; any other response ends the loop.
3. `Error class`: a Choice on `$.caught.Error`. `States.Timeout` and
   `Sandbox.Timedout` have class `timeout`; any other error has class
   `crash`.
4. `Check retry`: a Pass that computes
   `States.ArrayContains($.retry.retry_on, $.class)` and
   `States.ArrayLength($.retry.waits)`.
5. `Retry?`: a Choice. If the class is retryable and `$.attempt` is less
   than the number of waits, `Next attempt` rebuilds the state as `index`,
   `call`, `retry`, `attempt + 1` (`States.MathAdd`) and `wait`
   (`States.ArrayGetItem($.retry.waits, $.attempt)`). A `Wait` on
   `SecondsPath` = `$.wait` leads back to `Attempt`.
6. Otherwise the last attempt failed. In `slay-retry`, `Dead letter?` is a
   Choice: if `$.retry.dead_letter` is present and true, a `sqs:sendMessage`
   Task sends a dead letter, then the loop ends. `Send raised` handles
   class `failure` and `Send lambda error` the other classes; their
   `ResultPath` is null, so the state does not change. Without the flag,
   and in the fan-out's loop, the loop ends at once.

A dead letter's `MessageBody` is
`{execution: $$.Execution.Id, call: $.call, response: <the output's
response>}`. The success path does not pass `Dead letter?`, so a result
costs no extra state transition.

**Output:** the execution always succeeds, so a Map item processor can
report every item. The output is `{index: $.index, response: ...}`. The
response is the handler's response for a result or a `failure`. For a
`timeout` or a `crash` it is:
```
{status: "lambda_error", errorType: $.caught.Error, errorMessage: $.caught.Cause}
```
The loop ends in a Succeed state for this case too: a Fail state cannot
re-emit an error name such as `States.Timeout`.

`LambdaBackend` decodes the response as it decodes a direct invoke. For
`lambda_error`, a cause that holds a Lambda error payload (JSON with
`errorType`, `errorMessage` and `stackTrace`) is reported as a
`FunctionError` is. Any other cause becomes the `message`, and the error
name becomes the `error_type`. An execution that ends in another status
(`FAILED`, `TIMED_OUT`, `ABORTED`) raises `RemoteExecutionError` with the
execution's `error` and `cause`.

**Testing:** moto runs the synthesized definition with LocalStack's ASL
interpreter (`moto[stepfunctions]`). The tests replace the interpreter's
Lambda client with the in-process handler, so the loop, the handler and
`LambdaBackend` run together without AWS.

#### 11. Fan-out State Machine (`infrastructure/stacks/fanout.py`)

`slay-fanout` is a Standard state machine with a fixed name. It holds one
Distributed Map whose item processor is the retry loop of section 10,
without `Delayed?`, `Wait until` and the dead-letter states. Its `Retry?`
leads to `Give up` directly.

**Input** (written by `LambdaBackend.start_many`):
```
{
  items_key: "fanout/<execution name>/items.json",
  func_ref: ref_to_dict(func_ref),
  retry: {waits, retry_on, timeout},   # as in section 10; no waits without add_retry
  max_concurrency: max_concurrent or 0,# 0: no limit of slay's own
  item_count: len(args_list)           # the Map ignores it; job_results reads it
}
```

The client writes the items file first: a JSON array with one
`{args_refs, kwargs_refs}` object per call, as `encode_call` writes them.

**The Map:**
- `ItemReader` reads the items file from the storage bucket (`Key.$` =
  `$.items_key`).
- `ItemSelector` builds each child's input: `index` from
  `$$.Map.Item.Index`, `call` from `$.func_ref` and the item's
  `args_refs` and `kwargs_refs`, `retry` from `$.retry`, and `attempt` 0.
- `MaxConcurrencyPath` = `$.max_concurrency`.
- The children are Standard executions (`ProcessorConfig`: `DISTRIBUTED`,
  `STANDARD`). An Express child stops after 5 minutes; the Lambda runs
  for up to 14.
- No `ToleratedFailurePercentage`: its default of 0 fails the Map when a
  child fails. A child fails only for a Step Functions reason, because
  the loop reports a function's error as output.
- `ResultWriter` writes the child outputs to `fanout/results/` in the
  bucket. The execution's output names the manifest
  (`ResultWriterDetails.Key`), whose `ResultFiles.SUCCEEDED` lists the
  result files. Each file is a JSON array of entries whose `Output` is a
  child's output as JSON text. A Map without `ResultWriter` returns all
  child outputs in one state, which holds at most 256 KB.

`job_results` reads the manifest and the result files, places each
`response` at its `index`, and decodes the responses as in section 10. If
any item failed, it raises the error of the lowest failing index. An
index below the input's `item_count` with no response raises
`RemoteExecutionError`. The job ID is only the execution ARN, so the
count comes from the execution's input (`DescribeExecution`).

`ResultWriterV2` renders a `ResultWriter` only when the CDK feature flag
`@aws-cdk/aws-stepfunctions:useDistributedMapResultWriterV2` is on.
`ComputeStack` sets the flag in its own context, so the stack needs no
entry in `cdk.json`.

The items file, the bucket name in `ItemReader` and `ResultWriter`, and
the executor's `SLAY_STORAGE_BUCKET` are the same bucket, so the client's
`SLAY_STORAGE_BUCKET` must name it. `start_many` raises `StorageError`
when it names a directory.

**Testing:** moto's `ResultWriter` writes the manifest but no result
files. The test fixture writes them as AWS does, from the child outputs.

**Jobs:** `start_apply(fn, items, max_concurrent=None)` in `api.py`
returns a `Job(id, backend)`; `attach(job_id)` builds one for the current
backend. If `fn` is a `Retried` wrapper, `start_apply` passes its inner
function and the policy to `start_many`; any other wrapper goes to the
executors as the function. `apply()` keeps the client-side wrapper path
of section 7.

#### 12. Deferred Calls and Schedules (`deferred.py`, `backends/schedules.py`)

**API** (`deferred.py`, exported by `slay`):
```python
run_later(fn, /, *args, **kwargs) -> Job
run_at(when, fn, /, *args, **kwargs) -> Job
schedule(name, expression, fn, /, *args, **kwargs) -> None
reschedule(name, expression, fn, /, *args, **kwargs) -> None
unschedule(name) -> None
schedules() -> list[str]
```

- The parameters before `/` are positional-only, so every keyword goes to
  the function.
- Each call makes References of the arguments as `run()` does, and splits
  an `add_retry` function with `without_retry`, as `start_apply` does.
  Any other wrapper goes to the executor as the function.
- `run_at` turns `when` into an aware UTC datetime: a `timedelta` is
  added to the current time, and a `datetime` goes through
  `astimezone(timezone.utc)`, which reads a naive one as local time. A
  `when` of another type raises `TypeError`. A time 365 days or more
  ahead raises `ValueError`: Step Functions stops an execution after one
  year. A time in the past is allowed.
- `run_later` and `run_at` call `backend.start_one` and return
  `Job(job_id, backend)`. `Job.result()` calls `backend.job_result`.
- `schedule` and `reschedule` check the name against
  `[0-9A-Za-z_.-]{1,64}` (EventBridge Scheduler's rule) and raise
  `ValueError` otherwise. They call `backend.schedule` with
  `replace=False` and `replace=True`.
- `unschedule` calls `backend.unschedule`; `schedules` returns
  `backend.schedule_names()`.

**Jobs of one call (`LambdaBackend`):**
- `start_one` starts `slay-retry` with `{index: 0, call, retry, attempt:
  0}`, whose `retry` holds `dead_letter: true`, plus `at` when given. It rounds `at` up to a
  whole second and formats it as `%Y-%m-%dT%H:%M:%SZ`.
- `job_result` polls the execution as `execute` does, then decodes the
  output's `response`. Any `slay-retry` execution is a job of one call,
  so `attach()` also finds a retried `run()` or a scheduled run.
- `job_results` and `job_result` read the state machine name from the job
  ID (`arn:aws:states:<region>:<account>:execution:<machine>:<name>`).

**Schedules (`backends/schedules.py`, used by `LambdaBackend`):**
- Every request goes to the Scheduler group `slay`. A schedule's target
  is the `slay-retry` ARN, with `RoleArn` = the `slay-scheduler` ARN and
  `Input` = the JSON of `{index: 0, call, retry, attempt: 0}`, whose
  `retry` holds `dead_letter: true`. `FlexibleTimeWindow` is `OFF`. slay sets no time
  zone (UTC), no `ActionAfterCompletion` and no retry policy of its own,
  so Scheduler's defaults apply.
- The bucket is `open_bucket()`, so a directory raises `StorageError`
  before anything changes.
- Copies: `pin(ref, name)` returns a Reference that lasts.
  - An inline Reference is returned as it is.
  - A Reference stored in S3 is copied in the bucket (`CopyObject`, no
    download) to `schedules/<name>/<hash>.bin`, and returned with that
    `s3_key`.
  - A wrapper (section 7) is fetched, its `inner` is pinned, and the
    policy with the pinned `inner` is stored as a new value, then pinned.
    The new policy has a new hash. `Cached` keys its entries by the
    hash of its own `inner`, so a cache wrapper keeps its keys, but
    `add_caching(add_retry(fn))` keys by the pinned retry wrapper.
- `schedule(..., replace=False)`: `GetSchedule` first; a schedule of
  that name raises `ValueError` before anything is copied. Then it pins
  the function and the arguments, and calls `CreateSchedule`.
- `schedule(..., replace=True)`: it pins first, then calls
  `UpdateSchedule`, or `CreateSchedule` if no schedule has the name. Last,
  it deletes each object under `schedules/<name>/` that the new call does
  not name. A run that starts during the update finds its objects.
- `unschedule`: `DeleteSchedule`, then `ListObjectsV2` and
  `DeleteObjects` under `schedules/<name>/`. If the schedule did not
  exist, it raises `ValueError` after the objects are gone.
- `schedule_names`: `ListSchedules` over every page, sorted.
- A Scheduler error other than these raises `RemoteExecutionError`,
  chained from the boto3 error.

**Testing:** moto keeps schedules but never runs them. The tests take a
schedule's `Target.Input` from `GetSchedule` and start `slay-retry` with
it in moto, after they delete the original S3 values, so a run uses the
copies. moto's `Wait` compares a UTC timestamp with the local time without
a zone, so the tests that wait set `TZ=UTC`.

## Data Flow

### Remote Execution Flow

```
User Code
  ↓
func_ref = slay.put(func)
slay.run(func_ref, *args, **kwargs)        # or func_ref(*args, **kwargs)
  ↓
each arg: Reference as is, else create_reference(arg) → args_refs
each kwarg: Reference as is, else create_reference(value) → kwargs_refs
  ↓
serialize(value) → bytes → inline (< 1KB) or S3
  ↓
get_backend() → LambdaBackend
  ↓
lambda_backend.execute(func_ref, args_refs, kwargs_refs)
  ↓
boto3.invoke_lambda({
  func_ref: {...},
  args_refs: [{...}],
  kwargs_refs: {name: {...}}
})
  ↓
Lambda Handler
  ├─ Resolve func_ref → fetch (inline or S3) → deserialize
  ├─ Resolve args_refs → fetch → deserialize
  ├─ Resolve kwargs_refs → fetch → deserialize
  ├─ Execute: result = func(*args, **kwargs)
  ├─ Serialize result → inline or S3
  └─ Return result_ref
  ↓
Return result_ref to user
  ↓
result_ref.get()                           # when the user needs the value
  ├─ Fetch inline data, or from S3 via result_ref.s3_key
  └─ Deserialize using result_ref.type_info
```

### Parallel Execution Flow

```
slay.apply(func_ref, items)
  ↓
for item in items:
  item as is if it is a Reference, else create_reference(item) → item_ref
  args_list.append(([item_ref], {}))
  ↓
lambda_backend.execute_many(func_ref, args_list, retry, max_concurrent)
  ├─ write fanout/<name>/items.json to the bucket
  ├─ start slay-fanout with func_ref, retry and max_concurrency
  │    └─ Distributed Map: one child per item, each the retry loop
  │         └─ ResultWriter → fanout/results/<map run>/
  ├─ poll DescribeExecution until it ends
  ├─ read the manifest and the result files; order by index
  ├─ Any error: raise the error of the lowest failing index
  └─ Return list in input order
  ↓
Return [result_ref1, result_ref2, ...]     # slay.get_many() fetches them
```

## Key Design Decisions

### 1. Content Addressing

**Why SHA256?**
- Deterministic: Same input = same hash
- Enables deduplication: Same data uploaded once
- Collision resistant: Safe to use as identifier
- Part of reference: No separate lookup needed

**Impact:**
- Automatic deduplication (same arguments = cache hit)
- Storage efficient (no duplicate data)
- Location-independent (hash is universal identifier)

### 2. Lazy Loading

**Why return a Reference instead of the value?**
- Avoids fetching data until needed
- A result can feed the next call without a download
- Supports lazy composition of references
- Aligns with reference semantics

```python
ref = fn(data)         # Reference to the result; nothing is downloaded
other = fn2(ref)       # ref goes to the backend as a reference
result = other.get()   # Fetch when needed
```

### 3. Dill for Serialization

**Why not pickle?**
- Pickle can't serialize functions defined in `__main__`
- Pickle can't handle closures properly

**Why not cloudpickle?**
- Dill is slightly more flexible
- Similar capabilities for our use case
- Can swap later if needed

**Format Detection:**
- JSON first (most efficient), only when the round trip keeps every type
- Detect pandas/numpy (special handling)
- Fallback to dill (universal)

### 4. Immutable References

**Why frozen dataclass?**
- Guarantees references don't change
- Enables use as dict keys or set members
- Makes identity reliable

### 5. Backend Abstraction

**Why pluggable backends?**
- Future: Fargate, EC2, Kubernetes support
- Testing: Local backend for unit tests
- Flexibility: Easy to swap based on needs
- Extensibility: Users can implement custom backends

### 6. Step Functions for Fan-out

**Where the I/O happens.** The user pays only for I/O on the client's own
link, from a laptop or a phone to AWS. That link is slow and can drop.
Inside AWS, the handler fetches every argument from S3 before the function
starts, so the function's own code does no I/O for its arguments. A
fan-out must therefore use the client's link a fixed number of times,
whatever the number of items.

- With `slay-fanout`, the client sends each value it stores with `put`
  once, because storage is content-addressed. It then sends one items file
  of References, one `StartExecution` and the status polls.
- The client receives the manifest and the result files, which also hold
  References. A value crosses the link again only when the client calls
  `.get()`.
- Every per-item invoke, argument fetch and result write runs between Step
  Functions, Lambda and S3, in the region. The I/O spreads over the
  executors.
- With client threads, each item costs one Invoke request and one response
  on the client's link. The client must stay connected until the last item
  ends.

**Why a Distributed Map instead of client threads?**
- The client's link carries a fixed number of calls, not one per item (see
  above)
- The client may exit while the work goes on (see Jobs in section 11)
- Step Functions limits concurrency (`max_concurrent`) and retries each
  item with the same loop as `add_retry`
- A nested `apply()` in an executor needs no threads in that executor

**Why Standard children, not Express?** Old slay ran each `apply` as one
synchronous Express execution (`StartSyncExecution`). One request returned
the results, and Express bills by execution and duration, not by state
transition. Express gives up durability:
- an Express execution stops after 5 minutes, and the executor runs for up
  to 14;
- a synchronous Express execution runs at most once and keeps no state, so
  no job outlives the client;
- a Distributed Map cannot run inside an Express workflow, so the parent
  must be Standard in any case.

A Standard child costs about four state transitions per item: the Map
iteration, `Attempt`, `Raised?` and `Returned`. At the us-east-1 price of
$0.000025 a transition, that is $0.0001 per item. An Express child of a
5-second call at 64 MB costs about a third of that. However, an Express
child that needs more than 5 minutes, retry waits included, fails. Express
children may come later, for short calls, if their durability risks prove
acceptable.

## Limitations & Trade-Offs

### Current Limitations

1. **Execution Time**: Max 15 minutes (Lambda limit)
   - Future: Fargate backend for longer tasks
   - Workaround: Break into smaller tasks

2. **Memory**: Max 3GB (Lambda limit)
   - Future: Fargate or EC2 for larger workloads

3. **Concurrency**: Limited to Lambda concurrency (default ~1000)
   - Can request increase from AWS
   - Or use multiple AWS accounts

### Design Trade-Offs

1. **Simplicity vs Control**
   - Chose simplicity: Users don't manage infrastructure
   - Trade-off: Less fine-grained control

2. **Content Addressing vs Versioning**
   - Chose content addressing: Automatic deduplication
   - Trade-off: Need wrapper for version management

3. **Dill vs Cloudpickle**
   - Chose dill: More flexible
   - Trade-off: Slightly larger pickles, minor perf impact

4. **Monolithic Lambda vs Distributed Workers**
   - Chose monolithic: Simpler deployment, lower cost
   - Trade-off: No direct worker-to-worker communication

## Testing Strategy

### Unit Tests
- Reference system: Creation, hashing, immutability
- Serialization: Round-trip for all formats
- Storage: Tier selection, local filesystem fallback
- API: put, get, get_many, run, apply basic functionality
- Wrappers: Retry, caching composition
- **Coverage Goal**: > 80%

### Integration Tests (Future)
- Lambda backend with mocked AWS (moto)
- E2E: Local → Lambda → Local
- Multi-backend: Same test suite, different backends

### Example Tests
- All examples run without errors
- Results match expected output
- Examples demonstrate key features

## Future Enhancements

### Phase 2: Orchestration
- Step Functions integration
- DAG execution
- Conditional workflows
- Parallel job management

### Phase 3: Advanced Backends
- Fargate backend (longer tasks)
- EC2 backend (custom hardware)
- Kubernetes backend (on-premises)

### Phase 4: Advanced Wrappers
- Rate limiting (distributed counter in DynamoDB)
- Concurrency control (distributed semaphore)
- Partial application (pre-bind arguments)
- Circuit breaker pattern

### Phase 5: Observability
- CloudWatch dashboards
- X-Ray tracing
- Custom metrics
- Cost tracking per execution

### Phase 6: Performance
- Connection pooling
- Result streaming
- Incremental execution
- Adaptive timeout management

## Getting Started

### Development Setup
```bash
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
pytest tests/
```

### Local Testing
```python
import slay

def my_func(x):
    return x * 2

fn = slay.put(my_func)
print(fn(21).get())  # 42, uses the local backend
```

### AWS Deployment
```bash
cd infrastructure
pip install -r requirements.txt
cdk deploy

# Now use Lambda backend
import slay
slay.set_backend('lambda')
result = slay.put(my_func)(21).get()
```

## References

- [README.md](README.md) - Usage guide and examples
- [AWS CDK Docs](https://docs.aws.amazon.com/cdk/)
- [Dill Documentation](https://dill.readthedocs.io/)
