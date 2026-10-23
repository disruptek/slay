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
  def execute(func_ref, args, kwargs) -> Reference
  # args_list: list of (args, kwargs) pairs shaped as above
  def execute_many(func_ref, args_list) -> List[Reference]
  
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

**Lambda Backend (`backends/lambda_.py`):**
- Invokes AWS Lambda function
- ThreadPoolExecutor for parallel invocations
- 15-minute timeout per execution
- 1000+ concurrent executions (account dependent)

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
3. Call backend.execute(func_ref, args_refs, kwargs_refs)
4. Return the result Reference; the caller fetches it with `.get()`

Top-level Reference arguments pass through to the backend unchanged, and the
backend resolves them. Results can therefore feed the next call without a
download. A Reference inside a container is not resolved.

#### 7. Function Composition (`wrappers.py`)

Layer behaviors without modifying original function.

**Current Wrappers:**
- `add_retry()`: Automatic retry with exponential backoff
- `add_caching()`: Result memoization

**Design Pattern:**
```python
base_ref = create_reference(my_func)
retried = add_retry(base_ref, attempts=5)
cached = add_caching(retried)

# retried and cached are NEW references, not modified versions
# my_func is unchanged
```

**Wrapper Implementation:**
1. Get original function via `.data` property
2. Create wrapper function with new behavior
3. Return new reference to wrapper
4. Chain as needed

**Memoization Cache:**
```
Key: hash(function_ref) + ":" + hash(args) + ":" + hash(kwargs)
Value: Result object

Same function + same arguments = cache hit
```

#### 8. Lambda Runtime (`runtime/handler.py`)

Code that runs inside AWS Lambda.

**Flow:**
1. Receive event with serialized function and arguments
2. Fetch data from S3 via references
3. Deserialize using type information
4. Execute function
5. Serialize result
6. Store in S3
7. Return result reference

**Error Handling:**
- Capture stdout/stderr
- Include full traceback
- Store everything for debugging

**Packaging:** `cdk deploy` builds the Lambda code on the deploying machine,
without Docker. The bundle holds `runtime/handler.py`, a copy of `src/slay`,
and the packages in `runtime/requirements.txt`. pip installs them for the
Lambda platform and the runtime's Python version. The asset hash comes from
the bundle, so a change under `src/slay` produces a new deployment.

#### 9. AWS Infrastructure (`infrastructure/`)

CDK-based infrastructure deployment.

**Stacks:**
- **StorageStack**: S3 bucket + DynamoDB (optional)
- **ComputeStack**: Lambda function with IAM role

**Design:**
- Single Lambda function for all executions
- The Lambda Python runtime is the minor version of the Python that runs
  `cdk deploy`, because dill values load only under the version that wrote
  them. Deploying with a version that Lambda does not offer fails at synth.
- Stack outputs: `StorageBucket` (bucket name) and `ExecutorFunction`
  (function name)
- S3 bucket for data storage
- Automatic lifecycle policies (tier cold data)
- CORS enabled for future web access

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
ThreadPoolExecutor(max_workers=min(len(items), 500))
  ├─ Submit execute() for each (args_refs, kwargs_refs)
  ├─ as_completed() collects results
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

### 6. Threading for Parallel Invocation

**Why ThreadPoolExecutor for Lambda?**
- Lambda invocation is I/O bound (network)
- GIL doesn't block I/O
- Simple and reliable
- Scales to hundreds of concurrent tasks
- Future: Switch to async if needed

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

4. **max_concurrent not implemented**
   - MVP limitation
   - Future: Step Functions integration

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
