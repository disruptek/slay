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

6. **Function Composition** (`wrappers.py`)
   - `add_retry()` - automatic retry with backoff
   - `add_caching()` - result memoization
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
│   ├── wrappers.py            # Function composition
│   └── exceptions.py          # Exception types
├── infrastructure/            # AWS CDK infrastructure
│   ├── app.py                 # CDK app
│   ├── stacks/                # CDK stacks
│   │   ├── storage.py         # S3 + DynamoDB
│   │   └── compute.py         # Lambda functions
│   └── cdk.json               # CDK config
├── runtime/                   # Lambda runtime
│   ├── handler.py             # Lambda entry point
│   └── requirements.txt
├── tests/                     # Test suite
│   ├── unit/                  # Unit tests
│   └── integration/           # Integration tests
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
- **max_concurrent parameter**: Not yet implemented
  - Use Fargate or custom Step Functions for concurrency control

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
- Orchestration: ~$0.25 (Step Functions)
- Storage: Variable (depends on data size)
- **Total: ~$0.67 for 10,000 executions**

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

**`apply(fn: Reference, items: list[Any]) -> list[Reference]`**
Apply function to multiple items, returns references to results.
Reference items pass through without a download.

### Reference Methods

**`ref.get() -> Any`**
Fetch data from reference (with local caching).

**`fn(arg1, arg2, ...) -> Reference`**
Execute function reference remotely, returns reference to result.
Same as `run(fn, arg1, arg2, ...)`.

**`ref1 == ref2`**
Compare by content hash (content-addressed equality).

### Wrappers

**`add_retry(fn, attempts=3, backoff=1.0) -> Reference`**
Wrap function with automatic retry logic.

**`add_caching(fn, ttl=None) -> Reference`**
Wrap function with result caching/memoization.

### Configuration

**`slay.set_backend(name: str)`**
Set execution backend: `'local'` (default) or `'lambda'`.

**`slay.get_backend()`**
Get current backend.

## Contributing

This is an MVP implementation. Key areas for contribution:

1. **Additional Wrappers**: Rate limiting, concurrency control, cache-only mode
2. **Additional Backends**: Fargate, EC2, Kubernetes, Ray
3. **Orchestration**: Job tracking, durable execution, Step Functions
4. **Observability**: CloudWatch dashboards, X-Ray tracing
5. **Performance**: Parallel storage.load_many(), network optimizations
6. **Testing**: Integration tests, e2e tests, chaos engineering

## License

MIT
