# Slay Observability & Control Systems

Complete observability and production safety for distributed Slay execution.

## Quick Overview

### What's Implemented (Phases 1-2) ✅

- **Structured Logging** - Adaptive sampling, CloudWatch integration, JSON logs
- **X-Ray Tracing** (Optional) - Distributed tracing, service maps, parent-child segments
- **Auto-instrumentation** - boto3, requests, Lambda tracking

### What's Planned (Phases 3-5)

- **Metrics & Cost Tracking** - CloudWatch Metrics, real-time cost estimation
- **Control Systems** - Kill-switch, throttling, resource thresholds

> **`slay.control` is not wired in.** The package exists, but no slay code
> registers executions or checks for kill or throttle requests, and its
> registry lives in one process's memory. `kill_all()` and the other kill
> functions log at CRITICAL level and stop nothing, locally or on Lambda.
> To stop runaway Lambda work, set the reserved concurrency of
> `slay-executor` to 0:
> `aws lambda put-function-concurrency --function-name slay-executor --reserved-concurrent-executions 0`
- **Dashboards** - CloudWatch Dashboard, CLI helpers, Log Insights queries

---

## Phase 1: Structured Logging ✅

### Core Features

**Adaptive Sampling:**
- Errors: always logged (100%)
- Successes: sampled at 10% baseline
- High error rate (>5%): increase to 50%
- Critical error rate (>20%): increase to 100%

**Auto-Detection:**
- Lambda: CloudWatch Logs (with batching)
- Local: Structured JSON to stdout
- Falls back gracefully if CloudWatch unavailable

**Log Format (JSON):**
```json
{
  "timestamp": "2026-02-04T17:00:48.934457Z",
  "level": "INFO",
  "message": "put_complete",
  "service": "slay",
  "trace_id": "1-5e9c-abc123...",
  "ref_hash": "09d5e0df",
  "storage_tier": "inline",
  "function_hash": "abc123"
}
```

### Usage

**Get the logger:**
```python
from slay.observability import get_logger

logger = get_logger()
logger.info("Operation start", function_hash=fn.hash[:8])
logger.error("Something failed", error_type="TimeoutError")
```

**Set log level:**
```python
from slay.observability import set_log_level

set_log_level("DEBUG")  # Increase verbosity
set_log_level("ERROR")  # Only errors
```

**In Lambda:**
```
CloudWatch Log Group: /slay/executions/{function_name}
CloudWatch Log Stream: {request_id}-{timestamp}
Retention: 30 days (auto-configured)
```

---

## Phase 2: X-Ray Distributed Tracing (Optional) ✅

### How It Works

X-Ray is **completely optional**. If the SDK is not installed, all tracing gracefully becomes no-ops.

**Enable X-Ray:**
1. Install AWS X-Ray SDK: `pip install aws-xray-sdk`
2. Deploy to Lambda (daemon already running)
3. Grant IAM permissions (included in CDK)

**Disable X-Ray:**
- Simply don't install the SDK
- Code works exactly the same
- Zero tracing overhead

### Trace Structure

```
Trace ID: 1-5e9c-abc123...
Duration: 450ms

├─ slay.put (25ms)
│  ├─ serialize (8ms)
│  ├─ hash (3ms)
│  └─ storage (14ms) - inline tier
│
├─ slay.apply (380ms)
│  ├─ serialize_items (50ms)
│  └─ backend_execute_many (330ms)
│     ├─ AWS::Lambda::Invoke (batch 1) - 110ms
│     ├─ AWS::Lambda::Invoke (batch 2) - 105ms
│     └─ ...
│
└─ slay.get_many (45ms)
   ├─ load_many (42ms)
   └─ deserialize (3ms)
```

### Privacy-First Design

**What's traced:**
- Operation names (put, run, apply, get_many)
- Status (success/error)
- Hashes (first 8 chars only - `abc123...`)
- Sizes (bytes processed)
- Durations (ms)
- Error types (TimeoutError, StorageError, etc.)

**What's NOT traced:**
- Function source code
- Actual data values
- User credentials
- API keys or secrets

### CloudWatch Correlation

**X-Ray trace ID automatically injected into logs:**
```json
{
  "message": "put_complete",
  "trace_id": "1-5e9c-abc123...",
  "ref_hash": "abc123"
}
```

**In CloudWatch Console:**
1. View X-Ray trace
2. Click "View Logs" button
3. Logs automatically filtered by trace_id

### Auto-Instrumentation

When X-Ray SDK is installed, automatically traces:
- `boto3` calls (S3, Lambda, DynamoDB, CloudWatch)
- `requests` HTTP calls
- All AWS SDK operations

**No code changes needed.**

### Testing Locally

X-Ray requires daemon running on `localhost:2000`:

**Option 1: Docker**
```bash
docker run --name xray -d -p 2000:2000/udp amazon/aws-xray-daemon
```

**Option 2: Manual Installation**
```bash
# macOS
brew install aws-xray-daemon
xray --daemon

# Linux
# Download from AWS Console or compile from source
```

**Disable locally** (if not running daemon):
- Simply don't install X-Ray SDK
- Code works normally without tracing
- Zero overhead

---

## Observability in Action

### Example 1: Debugging a Failed Execution

**User observes:**
1. Function starts in Slay
2. CloudWatch Logs show `level="ERROR"`

**Debugging steps:**
1. Find error log entry
2. Copy `trace_id` value
3. Open X-Ray Console
4. Paste trace ID → See full execution flow
5. Click slow subsegment → see logs for that step

**Result:**
- Identified S3 timeout in subsegment (2.3 seconds)
- Fixed IAM policy
- Re-ran function
- Latency improved to 200ms

### Example 2: Performance Optimization

**Observing latency trend:**
1. CloudWatch Dashboard shows p95 latency: 2.5s
2. Open X-Ray Service Map
3. See 80% time in "serialize_items" subsegment
4. Realize serializing large DataFrames inefficiently
5. Switch from pickle to Parquet format
6. Re-test: p95 latency drops to 250ms

### Example 3: Understanding Cost

**Cost tracking (Phases 3-5):**
1. Dashboard shows cost rising: $0.87/day (threshold: $1.00)
2. Metrics show 100K executions/day
3. Breakdown: 70% Lambda, 20% S3, 10% CloudWatch
4. Optimize: reduce item size → fewer Lambda invocations
5. New cost: $0.42/day

---

## Implementation Details

### Logger Architecture

**File: `src/slay/observability/logger.py`**
- Thread-safe singleton: `get_logger()`
- Adaptive sampling per-function
- Graceful CloudWatch fallback
- Structured JSON output

**Features:**
- `logger.info(message, **extra)` - Info level, sampled
- `logger.error(message, **extra)` - Error level, always logged
- `logger.set_trace_id(trace_id)` - Link to X-Ray
- Auto-detect Lambda vs local

### X-Ray Architecture

**File: `src/slay/observability/xray.py`**
- Optional wrapper: `get_xray_recorder()`
- No-op fallback if SDK not available
- Thread-safe lazy initialization
- Privacy-safe annotations only

**Features:**
- `begin_segment(name)` - Top-level operation
- `begin_subsegment(name)` - Nested operation  
- `put_annotation(key, value)` - Indexed metadata
- `put_metadata(key, value)` - Non-indexed metadata
- `capture_error(exception)` - Exception tracking

### Tracing Integration

**File: `src/slay/observability/trace_context.py`**
- Context manager: `with trace_operation(...)`
- Automatic subsegment lifecycle
- Error capture and logging
- Timing metadata

**Usage:**
```python
with trace_operation("put", object_type="function"):
    ref = create_reference(fn)
```

**Errors are logged once:** blocks nest, for example `slay.put` around
`store_data` around `s3_put`. An exception that leaves several blocks is
logged once, by the innermost block, as `operation_failed: <name>`. The
outer blocks still record it in X-Ray. A different exception, such as a
wrapper raised by an outer block, gets its own log entry.

### Auto-Instrumentation

**File: `src/slay/observability/instrument.py`**
- Patches boto3, botocore, requests
- Runs on module import
- Graceful fallback if SDK missing
- Zero overhead if X-Ray disabled

---

## Configuration

### Environment Variables

```bash
# Logging
SLAY_LOG_LEVEL=INFO         # DEBUG, INFO, WARNING, ERROR, CRITICAL

# X-Ray (if SDK installed)
AWS_XRAY_SDK_ENABLED=true   # Enable/disable (auto-detected)
AWS_XRAY_DAEMON_ADDR=localhost:2000  # Local daemon address
```

### In Code

```python
from slay.observability import get_logger, set_log_level

# Set log level
set_log_level("DEBUG")

# Get logger
logger = get_logger()
logger.info("Custom message", custom_field="value")

# Check if X-Ray enabled
from slay.observability import is_xray_enabled
if is_xray_enabled():
    print("X-Ray tracing active")
```

---

## CloudWatch Integration

### Log Group Creation

Automatic on first Lambda execution:
- **Log Group:** `/slay/executions/{function_name}`
- **Log Stream:** `{request_id}-{timestamp}`
- **Retention:** 30 days
- **Format:** Structured JSON

### CloudWatch Insights Queries

**Find errors (last 24h):**
```
fields @timestamp, level, message, error_type
| filter level="ERROR"
| stats count() by error_type
```

**Latency analysis:**
```
fields @timestamp, duration_ms
| filter @message like /slay\.run/
| stats avg(duration_ms), pct(duration_ms, 95), pct(duration_ms, 99)
```

**Trace correlation:**
```
fields @timestamp, message, trace_id
| filter trace_id = "1-5e9c-abc123..."
| sort @timestamp asc
```

---

## Performance Impact

### Logging Overhead
- **Sampled logs:** ~0.1ms per operation
- **Error logs:** ~0.2ms (always captured)
- **Adaptive sampling:** 10% of successes = minimal cost

### X-Ray Overhead (if enabled)
- **Subsegment creation:** ~0.05ms
- **Annotation recording:** ~0.01ms
- **Auto-instrumentation:** ~1-2ms on first boto3 call

**Total typical overhead:** < 2ms per operation

---

## Next Steps (Phases 3-5)

### Phase 3: Metrics & Cost Tracking
- Real-time metrics collection
- CloudWatch Metrics integration
- Cost estimation and alerts
- Estimated: Week 3

### Phase 4: Control Systems
- Execution registry (DynamoDB)
- Hierarchical kill-switch
- Graceful throttling (not killing)
- Resource threshold monitoring
- Estimated: Week 4

### Phase 5: Dashboards & Polish
- CloudWatch Dashboard
- CLI helpers (`slay logs`, `slay trace`)
- Documentation and examples
- End-to-end testing
- Estimated: Week 5

---

## Troubleshooting

### Logs not appearing in CloudWatch

**Check:**
1. Are you in Lambda environment? (`AWS_LAMBDA_FUNCTION_NAME` env var)
2. Does Lambda have CloudWatch Logs permission?
3. Is boto3 installed?

**Fix:**
```python
from slay.observability import get_logger
logger = get_logger()

# Check if CloudWatch is available
handler = logger._get_cloudwatch_handler()
print(f"CloudWatch available: {handler is not None}")
```

### X-Ray traces not appearing

**Check:**
1. Is aws-xray-sdk installed? `pip list | grep xray`
2. In Lambda: X-Ray daemon auto-available
3. Locally: Is `localhost:2000` daemon running?

**Verify:**
```python
from slay.observability import is_xray_enabled
print(f"X-Ray enabled: {is_xray_enabled()}")
```

### High sampling rate increasing CloudWatch cost

**Adjust:**
- Base sampling rate: 10% (in `logger.py` - `base_rate`)
- Thresholds: 5% and 20% (configurable)
- Always: 100% of errors

**Note:** Adaptive sampling increases automatically on high error rates.

---

## Security & Privacy

**What's logged:**
- Operation names
- Hashes (non-reversible, first 8 chars)
- Sizes
- Types
- Timing
- Status
- Error types

**What's never logged:**
- Function source code
- Actual data values
- Credentials/secrets
- User input

**Log retention:**
- CloudWatch: 30 days (auto-expiry)
- X-Ray: 30 days (AWS default)
- Both: Delete before expiry on request

---

## Cost Analysis

**Monthly observability costs:**

| Component | Usage | Cost |
|-----------|-------|------|
| CloudWatch Logs | 1 GB/day (with sampling) | $5/month |
| CloudWatch Metrics | Custom metrics (Phase 3) | $6/month |
| X-Ray (if enabled) | 10K traces/day | Free tier |
| DynamoDB (Phase 4) | Execution registry | $2/month |
| **Total** | | **~$13/month** |

**With aggressive sampling (only errors):**
- Logs: < $1/month
- Total: < $8/month

---

## Summary

Slay now has **production-grade observability**:

✅ **Structured Logging** - All operations logged with context
✅ **Optional X-Ray** - Distributed tracing (can be disabled)
✅ **Auto-Instrumentation** - AWS SDK automatically traced
✅ **CloudWatch Integration** - Auto log group/stream creation
✅ **Trace Correlation** - X-Ray trace ID in logs
✅ **Privacy-First** - No sensitive data logged
✅ **Zero Overhead When Disabled** - X-Ray is truly optional

**Coming in Phases 3-5:**
- Real-time metrics and cost tracking
- Hierarchical kill-switch with throttling
- CloudWatch dashboards
- CLI tools for debugging

---

## Related Files

- **Logger:** `src/slay/observability/logger.py`
- **CloudWatch Handler:** `src/slay/observability/cloudwatch_logs.py`
- **X-Ray Wrapper:** `src/slay/observability/xray.py`
- **Trace Context:** `src/slay/observability/trace_context.py`
- **Auto-Instrumentation:** `src/slay/observability/instrument.py`
- **API Integration:** `src/slay/api.py` (logging added)
- **Storage Logging:** `src/slay/core/storage.py` (logging added)
