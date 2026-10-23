# Slay AWS Deployment Guide

This guide explains how to deploy the Slay infrastructure to AWS using CDK.

## Prerequisites

1. **AWS Account**: You need an active AWS account
2. **AWS CLI**: Install from https://aws.amazon.com/cli/
3. **AWS Credentials**: Configure your AWS credentials (see below)
4. **Python 3.10+**: The Lambda runs the minor version of the Python that
   runs `cdk deploy`, so use the version that your clients use
5. **Node.js**: Required for CDK (typically installed with npm)

## Step 1: Configure AWS Credentials

### Option A: Using AWS CLI (Recommended)

```bash
# Configure interactive setup
aws configure

# You'll be prompted for:
# - AWS Access Key ID
# - AWS Secret Access Key
# - Default region (e.g., us-east-1)
# - Default output format (json, table, or text)
```

**Get your credentials:**
1. Go to https://console.aws.amazon.com/iam/
2. Click "Users" in the left sidebar
3. Select your user
4. Go to "Security credentials" tab
5. Click "Create access key"
6. Copy the Access Key ID and Secret Access Key

### Option B: Using Environment Variables

```bash
export AWS_ACCESS_KEY_ID="your-access-key"
export AWS_SECRET_ACCESS_KEY="your-secret-key"
export AWS_DEFAULT_REGION="us-east-1"
export AWS_ACCOUNT_ID="123456789012"  # Your 12-digit AWS account ID
```

### Option C: Using Named Profiles

```bash
# Configure a named profile
aws configure --profile slay

# Then use it when deploying
export AWS_PROFILE=slay
```

## Step 2: Find Your AWS Account ID

```bash
# If credentials are configured, this will show your account ID:
aws sts get-caller-identity

# Output will look like:
# {
#     "UserId": "AIDAI...",
#     "Account": "123456789012",
#     "Arn": "arn:aws:iam::123456789012:user/your-user"
# }
```

**If you get an error:**
- Check that your credentials are valid
- Verify AWS CLI is installed: `aws --version`

## Step 3: Deploy Infrastructure

### Navigate to Infrastructure Directory

```bash
cd infrastructure

# Install Python dependencies
pip install -r requirements.txt

# If you don't have a requirements.txt, install aws-cdk:
pip install aws-cdk-lib aws-cdk-lib constructs
```

### Deploy the Stacks

```bash
# Option A: Using environment variables (all regions except us-east-1)
export CDK_DEFAULT_ACCOUNT="123456789012"
export CDK_DEFAULT_REGION="us-east-1"
cdk deploy

# Option B: Using context flags (all regions)
cdk deploy -c account=123456789012 -c region=us-east-1

# Option C: Using AWS CLI environment variables
export AWS_ACCOUNT_ID="123456789012"
cdk deploy
```

### Verify Deployment

```bash
# Check CloudFormation stacks
aws cloudformation list-stacks --region us-east-1

# Check S3 buckets
aws s3 ls

# Check Lambda functions
aws lambda list-functions --region us-east-1
```

## Step 4: Configure Your Application

### Update Environment Variables

Create a `.env` file in your project root. `SLAY_STORAGE_BUCKET` is the
`StorageBucket` output of `SlayStorageStack`; slay does not detect it.
Keep `.env` out of version control:

```bash
SLAY_BACKEND=lambda
SLAY_STORAGE_BUCKET=slay-storage-123456789012-us-east-1
AWS_REGION=us-east-1
```

### Update Your Code

```python
import slay

# Set the backend to use Lambda
slay.set_backend('lambda')

# Now your functions run on AWS Lambda
result = slay.put(my_function)(data).get()
```

## Troubleshooting

### Error: "Unable to resolve AWS account to use"

**Solution**: Set one of these:

```bash
# Option 1: Set environment variable
export CDK_DEFAULT_ACCOUNT="123456789012"

# Option 2: Use context flag
cdk deploy -c account=123456789012

# Option 3: Configure AWS CLI
aws configure
```

### Error: "The security token included in the request is invalid"

**Solution**: Your AWS credentials are incorrect or expired.

```bash
# Verify credentials
aws sts get-caller-identity

# If this fails, reconfigure
aws configure
```

### Error: "User is not authorized to perform: iam:..." or other permission errors

**Solution**: Your AWS user doesn't have sufficient permissions.

See [AWS_IAM_SETUP.md](AWS_IAM_SETUP.md) for complete instructions on:
- Setting up required IAM permissions
- Using the provided `IAM_POLICY.json` template
- Troubleshooting specific permission errors

**Quick fix**: Attach the pre-built policy:
```bash
# Replace YOUR_ACCOUNT_ID with your 12-digit AWS account ID
policy=$(mktemp)
sed "s/ACCOUNT_ID/YOUR_ACCOUNT_ID/g" IAM_POLICY.json > "$policy"

aws iam put-user-policy \
  --user-name your-username \
  --policy-name SlayDeployment \
  --policy-document "file://$policy"
rm -f "$policy"
```

Or contact your AWS administrator and share the [AWS_IAM_SETUP.md](AWS_IAM_SETUP.md) guide with them.

### Error: "Stack outputs are not available"

**Solution**: Wait for the deployment to complete. Check progress:

```bash
# Watch CDK deployment
cdk deploy --no-rollback

# Or check CloudFormation in AWS Console
# https://console.aws.amazon.com/cloudformation/
```

### S3 Bucket Already Exists

**Solution**: S3 bucket names are globally unique. If the deployment fails because the bucket name already exists, you have a few options:

1. **Use a different region** (bucket names include region)
2. **Use a different account**
3. **Destroy the existing stack**:
   ```bash
   cdk destroy
   ```

## Security Best Practices

1. **Never commit credentials** to version control
2. **Use AWS IAM roles** for production deployments
3. **Rotate access keys** regularly
4. **Use `aws configure` with MFA** for enhanced security
5. **Keep CDK and dependencies updated**:
   ```bash
   npm update -g aws-cdk
   pip install --upgrade aws-cdk-lib
   ```

## Cleanup

To delete all AWS resources and stop incurring charges:

```bash
cd infrastructure

# Destroy CDK stacks
cdk destroy

# Or manually delete via AWS Console
# https://console.aws.amazon.com/cloudformation/
```

## Next Steps

After successful deployment:

1. Test your setup:
   ```bash
   python examples/01_hello_world.py
   ```

2. Check monitoring:
   - Lambda Dashboard: https://console.aws.amazon.com/lambda/
   - S3 Buckets: https://s3.console.aws.amazon.com/
   - CloudWatch Logs: https://console.aws.amazon.com/cloudwatch/

3. Configure auto-scaling (optional):
   - Adjust Lambda memory in `infrastructure/stacks/compute.py`
   - Set up CloudWatch alarms for monitoring

## Additional Resources

- [AWS CDK Documentation](https://docs.aws.amazon.com/cdk/)
- [AWS CLI Configuration](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-quickstart.html)
- [AWS IAM Best Practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)
- [Lambda Limits](https://docs.aws.amazon.com/lambda/latest/dg/limits.html)
