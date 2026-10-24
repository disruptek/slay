# Using Slay with AWS Lambda

This guide shows how to deploy slay to AWS and execute functions on Lambda.

## Prerequisites

You need:
- AWS account (with credentials configured)
- AWS CLI installed and configured
- Node.js 18+ (for AWS CDK)
- Python 3.10+, the same minor version that your clients use
- Network access to PyPI during `cdk deploy` (the bundle is built with pip)
- The slay repository cloned locally

## Step 1: Install AWS CDK and Dependencies

```bash
# Navigate to infrastructure directory
cd infrastructure

# Install Node dependencies for CDK
npm install -g aws-cdk

# Install Python dependencies
pip install -r requirements.txt

# Verify CDK installation
cdk --version
```

## Step 2: Configure AWS Credentials

Make sure your AWS credentials are configured:

```bash
# Option 1: Using AWS CLI
aws configure
# You'll be prompted for:
#   AWS Access Key ID
#   AWS Secret Access Key
#   Default region (e.g., us-east-1)
#   Default output format (json)

# Option 2: Using environment variables
export AWS_ACCESS_KEY_ID="your-access-key"
export AWS_SECRET_ACCESS_KEY="your-secret-key"
export AWS_DEFAULT_REGION="us-east-1"

# Verify credentials work
aws sts get-caller-identity
```

## Step 3: Deploy to AWS

The Lambda runs the Python minor version of the interpreter that runs
`cdk deploy`. Functions you send are dill values, and they load only under
the Python version that wrote them. Activate the virtualenv that your
clients use before you deploy, so `python3` is that interpreter.

`cdk deploy` builds the Lambda code locally, without Docker. It copies
`runtime/handler.py` and `src/slay`, and pip installs
`runtime/requirements.txt` for the Lambda platform.

```bash
# Use the clients' Python for the deployment
source venv/bin/activate

# From infrastructure directory
cd infrastructure

# Preview the changes that will be made
cdk diff

# Deploy the stacks
cdk deploy

# You'll see:
# - Confirmation to create CloudFormation stacks
# - Progress of deployment
# - Output values (including bucket name)
```

The deployment creates:
- **S3 Bucket**: `slay-storage-{account}-{region}` for data storage
- **DynamoDB Table**: `MetadataTable` for optional metadata
- **Lambda Function**: `slay-executor` for remote execution
- **IAM Role**: With permissions for S3 access

## Step 4: Use Lambda Backend in Your Code

Once deployed, point your clients at the storage bucket. `cdk deploy`
prints it as the `StorageBucket` output of `SlayStorageStack`. CDK sets the
variable inside the Lambda, but not on your machine:

```bash
export SLAY_STORAGE_BUCKET="slay-storage-{account}-{region}"
```

Then use the Lambda backend:

```python
import slay

# Set backend to Lambda (instead of local)
slay.set_backend('lambda')

# Now all functions execute on Lambda
def process_data(x):
    """This will run on AWS Lambda"""
    return x * 2

fn = slay.put(process_data)

# Execute remotely; fn(21) returns a Reference to the result
result = fn(21).get()
print(result)  # 42

# Process many items in parallel
items = list(range(1000))
results = slay.get_many(slay.apply(fn, items))
print(len(results))  # 1000
```

## Complete Example: From Local to Lambda

```python
import slay

# Define your function (works locally AND on Lambda)
def analyze_data(data):
    """Analyze some data"""
    total = sum(data)
    average = total / len(data)
    return {"total": total, "average": average}

fn = slay.put(analyze_data)

# Step 1: Test locally (no AWS needed)
slay.set_backend('local')
result = fn([1, 2, 3, 4, 5]).get()
print("Local result:", result)

# Step 2: Scale to Lambda (after cdk deploy)
slay.set_backend('lambda')
result = fn([1, 2, 3, 4, 5]).get()
print("Lambda result:", result)

# Step 3: Process thousands in parallel
large_dataset = [[1, 2, 3] for _ in range(1000)]
results = slay.get_many(slay.apply(fn, large_dataset))
print(f"Processed {len(results)} items on Lambda")
```

## Parallel Execution on Lambda

Lambda makes parallelism easy:

```python
import slay

slay.set_backend('lambda')

def process_item(item):
    # Each item runs on a separate Lambda invocation
    # No configuration needed!
    return item ** 2

# Process 10,000 items in parallel
items = list(range(10000))
results = slay.get_many(slay.apply(slay.put(process_item), items))

# How it works:
# - slay writes the 10,000 calls to the storage bucket and starts one
#   execution of the slay-fanout state machine
# - Its Distributed Map runs one child execution, and one Lambda
#   invocation, per item
# - Results are collected and returned in order
# - Concurrency is limited by the account's Lambda concurrency (typically
#   ~1000), or by max_concurrent:
results = slay.get_many(slay.apply(slay.put(process_item), items, max_concurrent=100))
```

SLAY_STORAGE_BUCKET must name the stack's S3 bucket. See the README
sections "Fan-out" and "Jobs".

## Function Composition with Lambda

All wrappers work seamlessly on Lambda:

```python
import slay

slay.set_backend('lambda')

def flaky_api_call(url):
    """This might fail, so we'll add retry"""
    import requests
    response = requests.get(url)
    return response.json()

# Add resilience
fn_ref = slay.put(flaky_api_call)
resilient = slay.add_retry(fn_ref, attempts=5, backoff=2.0)
cached = slay.add_caching(resilient)

# First call: Makes API request, retries on failure, caches result
result = cached("https://api.example.com/data").get()

# Second call: the client reads the cache entry from the storage bucket
# and makes no Lambda call
result2 = cached("https://api.example.com/data").get()
```

## Monitoring and Debugging

### View Lambda Logs

```bash
# Stream logs from the slay-executor function
aws logs tail /aws/lambda/slay-executor --follow

# View logs from a specific time
aws logs tail /aws/lambda/slay-executor --start "1 hour ago"
```

### Monitor Execution

When a function runs on Lambda, you can see:

```python
import slay

slay.set_backend('lambda')

try:
    result = slay.put(my_function)(data).get()
except ValueError as e:               # the function's own exception type
    remote = e.__cause__              # slay.RemoteExecutionError
    print("Remote traceback:", remote.remote_traceback)
    print("Remote logs:", remote.remote_logs)
except slay.RemoteExecutionError as e:
    # The Lambda failed outside the function, for example on a timeout
    print("Error:", e.error_type, e.message)
```

The `RemoteExecutionError` holds:
- The remote traceback text (`remote_traceback`)
- The stdout and stderr of the call (`remote_logs`)
- The exception's type name, or the Lambda `errorType` (`error_type`)

The README section "Errors" lists every case.

### Check S3 Storage

Your data is stored in S3:

```bash
# List stored references
aws s3 ls s3://slay-storage-{account}-{region}/refs/

# View a specific object
aws s3 cp s3://slay-storage-{account}-{region}/refs/ab/abc123.bin ./debug.bin
```

## Performance Characteristics

### Lambda Execution

| Metric | Value | Notes |
|--------|-------|-------|
| Cold start | 100ms - 1s | First invocation in a while |
| Warm invocation | 100-500ms | Subsequent invocations |
| Max execution | 15 minutes | Hard limit |
| Max memory | 3GB | Also affects CPU |
| Max concurrent | ~1000 | Per AWS account (adjustable) |
| Cost | ~$0.0000166667/GB-sec | Plus storage costs |

### Cost Calculation

Example: Process 10,000 items, each takes 5 seconds, 512MB memory

```
Compute cost:
  10,000 invocations × 0.5GB × 5 sec × $0.0000166667 = $0.42

Orchestration cost (Step Functions, Standard):
  10,000 items × about 4 state transitions × $0.000025 = $1.00

Storage cost (1GB data):
  ~$0.023/month (negligible for short-term storage)

Total: ~$1.42 for 10,000 parallel executions
```

## Troubleshooting

### "Lambda function not found"

```
Error: Lambda function 'slay-executor' not found
```

**Solution**: Run `cdk deploy` to create the function.

### "SLAY_STORAGE_BUCKET is not set"

```
StorageError: SLAY_STORAGE_BUCKET is not set. ...
```

A value of 1 KB or more needs storage, and the client has no bucket set.

**Solution**: Set the environment variable to the `StorageBucket` output
of `SlayStorageStack`:
```bash
export SLAY_STORAGE_BUCKET="slay-storage-{account}-{region}"
```

slay does not detect the bucket. For local runs, an existing directory
works as well.

### "serialized by Python 3.X, but this is Python 3.Y"

The client and the Lambda run different Python minor versions. Redeploy
from the client's virtualenv, so the Lambda runtime matches:
```bash
source venv/bin/activate
cd infrastructure && cdk deploy
```

### "Lambda has no Python 3.X runtime"

`cdk deploy` ran under a Python version that AWS Lambda does not offer.
Deploy, and run your clients, with a Python version that Lambda supports.

### "Permission denied" accessing S3

```
Error: An error occurred (AccessDenied)
```

**Solution**: The IAM role doesn't have S3 permissions. Check:
```bash
# Verify the Lambda execution role
aws lambda get-function-configuration --function-name slay-executor
# Look at Role field, then check that role's policies
```

### Lambda timeout

```
Error: Task timed out after 900 seconds
```

**Solution**: Your function took > 15 minutes. Either:
1. Optimize the function to run faster
2. Break it into smaller tasks
3. Use Fargate backend (in future phases)

## Advanced: Custom Lambda Configuration

You can modify the Lambda deployment in `infrastructure/stacks/compute.py`:

```python
# Change memory/CPU
memory_size=3008,  # 1769-10240 MB

# Change timeout
timeout=Duration.minutes(14),  # Up to 15 minutes

# Change ephemeral storage
ephemeral_storage_size=lambda_.Size.gibibytes(10),  # 512MB-10GB

# Add environment variables
environment={
    "MY_VAR": "value",
    "SLAY_STORAGE_BUCKET": storage_bucket.bucket_name,
}
```

Then redeploy:
```bash
cdk deploy
```

## Cleanup: Remove AWS Resources

When done, delete all resources:

```bash
# From infrastructure directory
cdk destroy

# Confirm deletion when prompted
# This removes:
# - Lambda function
# - S3 bucket (with data)
# - DynamoDB table
# - IAM roles
# - CloudWatch logs
```

**Note**: S3 bucket deletion may fail if it has data. Force delete if needed:

```bash
aws s3 rb s3://slay-storage-{account}-{region} --force
```

## Next Steps

1. **Optimize**: Tune memory/timeout for your workload
2. **Monitor**: Set up CloudWatch alarms for errors
3. **Scale**: Increase concurrency limits if needed
4. **Cost**: Use AWS Cost Explorer to track slay spending

## References

- [AWS Lambda Limits](https://docs.aws.amazon.com/lambda/latest/dg/limits.html)
- [AWS CDK Documentation](https://docs.aws.amazon.com/cdk/)
- [AWS Lambda Pricing](https://aws.amazon.com/lambda/pricing/)

## Examples

See the `examples/` directory for more:
- `01_hello_world.py` - Can use Lambda backend
- `02_parallel_processing.py` - Perfect for Lambda
- `03_retry_caching.py` - All features work on Lambda
