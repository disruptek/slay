# Lambda Quick Start (5 Minutes)

## TL;DR - Deploy and Use Lambda in 5 Steps

### Step 1: Install Dependencies
```bash
cd infrastructure
pip install -r requirements.txt
npm install -g aws-cdk
```

### Step 2: Configure AWS
```bash
aws configure
# Enter your AWS credentials
```

### Step 3: Deploy
```bash
# Run from the virtualenv your clients use: the Lambda gets its Python version
cdk deploy
# Wait 2-3 minutes for deployment
```

### Step 4: Use Lambda
```bash
# The StorageBucket output of cdk deploy; needed for values of 1 KB or more
export SLAY_STORAGE_BUCKET="slay-storage-{account-id}-{region}"
```
```python
import slay

slay.set_backend('lambda')

def my_func(x):
    return x * 2

result = slay.put(my_func)(21).get()
print(result)  # 42
```

### Step 5: Process in Parallel
```python
import slay

slay.set_backend('lambda')

square = slay.put(lambda x: x ** 2)
results = slay.get_many(slay.apply(square, list(range(100))))
print(results)  # [0, 1, 4, 9, ..., 9801]
```

---

## Common Commands

### Deploy Infrastructure
```bash
cd infrastructure
cdk deploy
```

### Remove Everything (Delete from AWS)
```bash
cd infrastructure
cdk destroy
```

### View Logs
```bash
aws logs tail /aws/lambda/slay-executor --follow
```

### Check What's in S3
```bash
aws s3 ls s3://slay-storage-{account-id}-{region}/refs/
```

---

## Code Examples

### Example 1: Simple Remote Execution
```python
import slay

slay.set_backend('lambda')

def greet(name):
    return f"Hello, {name}!"

result = slay.put(greet)("Lambda").get()
print(result)  # "Hello, Lambda!"
```

### Example 2: Parallel Processing (1000 items)
```python
import slay

slay.set_backend('lambda')

def process(item):
    return item ** 2

results = slay.get_many(slay.apply(slay.put(process), list(range(1000))))
print(f"Processed {len(results)} items")
```

### Example 3: With Retry (Resilient API Calls)
```python
import slay

slay.set_backend('lambda')

def fetch_data(url):
    import requests
    return requests.get(url).json()

# Add retry: up to 5 attempts with exponential backoff
fn_ref = slay.put(fetch_data)
resilient = slay.add_retry(fn_ref, attempts=5, backoff=2.0)

result = resilient("https://api.example.com/data").get()
print(result)
```

### Example 4: With Caching
```python
import slay

slay.set_backend('lambda')

def expensive_computation(x):
    # This takes time...
    return x ** x

fn_ref = slay.put(expensive_computation)
cached = slay.add_caching(fn_ref)

# First call: runs on Lambda, then writes the entry to the storage bucket
result1 = cached(5).get()

# Second call: the client reads the entry and makes no Lambda call
result2 = cached(5).get()
```

### Example 5: Combining Retry + Caching
```python
import slay

slay.set_backend('lambda')

def api_call(url):
    import requests
    return requests.get(url).json()

fn_ref = slay.put(api_call)
resilient = slay.add_retry(fn_ref, attempts=5)
cached = slay.add_caching(resilient)

# Full production setup: retries + caching
result = cached("https://api.example.com/data").get()
```

---

## Costs (Rough Estimates)

| Scenario | Cost |
|----------|------|
| 100 executions, 5 sec each, 512MB | ~$0.004 |
| 1,000 executions, 5 sec each, 512MB | ~$0.042 |
| 10,000 executions, 5 sec each, 512MB | ~$0.42 |
| 100,000 executions, 5 sec each, 512MB | ~$4.20 |

+ Storage costs (~$0.023/GB-month)

---

## Troubleshooting

| Error | Solution |
|-------|----------|
| `Lambda function not found` | Run `cdk deploy` |
| `SLAY_STORAGE_BUCKET is not set` | Set it to the `StorageBucket` output of `cdk deploy` |
| `AccessDenied` to S3 | Check IAM role permissions |
| `Task timed out after 900 seconds` | Function took > 15 min, break into smaller tasks |
| `serialized by Python 3.X, but this is Python 3.Y` | Redeploy from the client's virtualenv |
| `Credentials not found` | Run `aws configure` |

---

## Environment Variables

After deployment, these are automatically set in Lambda:
```
SLAY_STORAGE_BUCKET=slay-storage-{account}-{region}
```

Clients do not get this variable from the deployment. Set it yourself, as
in Step 4.

You can access them in your functions:
```python
import os
bucket = os.getenv('SLAY_STORAGE_BUCKET')
```

---

## What Happens Under the Hood

When you call `fn(*args)` (the same as `slay.run(fn, *args)`) with the
Lambda backend:

1. `slay.put(func)` already serialized `func` (converted it to bytes)
2. Each argument that is not a Reference already is serialized
3. Values of 1 KB or more go to S3; smaller ones travel inside the reference
4. Lambda function is invoked with the references
5. Lambda fetches `func` and the arguments
6. Lambda executes: `result = func(*args)`
7. Result stored the same way (inline or S3)
8. A Reference to the result is returned to you
9. `.get()` fetches and deserializes the result on your machine

All automatic - you just write normal Python!

---

## Next Steps

1. Read [LAMBDA_DEPLOYMENT.md](LAMBDA_DEPLOYMENT.md) for detailed info
2. Try the examples: `python examples/01_hello_world.py`
3. Set `slay.set_backend('lambda')` to use AWS
4. Check [README.md](README.md) for more documentation

---

## Need Help?

- **Deployment issues**: See [LAMBDA_DEPLOYMENT.md](LAMBDA_DEPLOYMENT.md)
- **API questions**: See [README.md](README.md)
- **Architecture details**: See [DESIGN.md](DESIGN.md)
