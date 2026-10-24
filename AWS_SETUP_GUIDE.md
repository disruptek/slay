# Complete AWS Setup Guide for Slay

This guide will help you get AWS set up for deploying Slay. Choose your path based on your experience level.

## 🚀 Quick Start (5 minutes)

If you have AWS credentials and just need the permissions:

```bash
# 1. Get your account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# 2. Apply permissions: a managed policy, attached to your user
policy=$(mktemp)
sed "s/ACCOUNT_ID/$ACCOUNT_ID/g" IAM_POLICY.json > "$policy"
aws iam create-policy \
  --policy-name SlayDeployment \
  --policy-document "file://$policy"
rm -f "$policy"
aws iam attach-user-policy \
  --user-name your-username \
  --policy-arn "arn:aws:iam::$ACCOUNT_ID:policy/SlayDeployment"

# 3. Deploy
cd infrastructure
cdk deploy
```

## 📋 Step-by-Step Setup

### Step 1: Create/Configure AWS Account

**If you don't have an AWS account:**
1. Go to https://aws.amazon.com/
2. Click "Create an AWS Account"
3. Follow the signup process

**If you have an account but no credentials:**
1. Go to https://console.aws.amazon.com/iam/
2. Click "Users" in the left sidebar
3. Click your username
4. Go to "Security credentials" tab
5. Click "Create access key"
6. Save your Access Key ID and Secret Access Key (you won't see this again!)

### Step 2: Configure AWS CLI

```bash
# Option A: Interactive (recommended)
aws configure

# You'll be prompted for:
# AWS Access Key ID: [paste from step 1]
# AWS Secret Access Key: [paste from step 1]
# Default region name: us-east-1 (or your preferred region)
# Default output format: json

# Option B: Using environment variables
export AWS_ACCESS_KEY_ID="your-key"
export AWS_SECRET_ACCESS_KEY="your-secret"
export AWS_DEFAULT_REGION="us-east-1"
```

**Verify it works:**
```bash
aws sts get-caller-identity
# Should print your account info
```

### Step 3: Set Up IAM Permissions

We provide a ready-to-use IAM policy that includes only the permissions needed to deploy and use Slay. It is too large for an inline user policy (2,048 characters), so create it as a managed policy.

**Option A: Use the provided policy (Recommended)**

```bash
# Get your account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "Your AWS Account ID: $ACCOUNT_ID"

# Create the policy with your account ID
policy=$(mktemp)
sed "s/ACCOUNT_ID/$ACCOUNT_ID/g" IAM_POLICY.json > "$policy"
aws iam create-policy \
  --policy-name SlayDeployment \
  --policy-document "file://$policy"
rm -f "$policy"

# Attach the policy to your user
aws iam attach-user-policy \
  --user-name your-username \
  --policy-arn "arn:aws:iam::$ACCOUNT_ID:policy/SlayDeployment"
```

**Option B: Via AWS Console**
1. Go to https://console.aws.amazon.com/iam/home#/policies
2. Click "Create policy"
3. Choose "JSON" editor
4. Copy the contents of `IAM_POLICY.json` (replace `ACCOUNT_ID` with your 12-digit account ID)
5. Name it `SlayDeployment` and click "Create policy"
6. Open your user, click "Add permissions" → "Attach policies directly", and select `SlayDeployment`

Users who only run slay need `IAM_POLICIES/SlayClient.json` alone; see [IAM_POLICIES/README.md](IAM_POLICIES/README.md).

**Option C: Use AWS Managed Policies** (easier but less secure)
```bash
aws iam attach-user-policy \
  --user-name your-username \
  --policy-arn arn:aws:iam::aws:policy/PowerUserAccess

aws iam attach-user-policy \
  --user-name your-username \
  --policy-arn arn:aws:iam::aws:policy/IAMFullAccess
```

**Verify permissions work:**
```bash
# All of these should succeed
aws s3 ls
aws lambda list-functions
aws dynamodb list-tables
aws cloudformation list-stacks
```

### Step 4: Set Up CDK Environment

```bash
# Set your account ID and region
export CDK_DEFAULT_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
export CDK_DEFAULT_REGION="us-east-1"  # or your preferred region

# Verify
echo "Account: $CDK_DEFAULT_ACCOUNT"
echo "Region: $CDK_DEFAULT_REGION"
```

### Step 5: Deploy to AWS

```bash
# Go to infrastructure directory
cd infrastructure

# Install dependencies
pip install -r requirements.txt
npm install -g aws-cdk

# Review what will be created
cdk diff

# Deploy (use --require-approval=never to skip approval)
cdk deploy --require-approval=never
```

**After deployment, you should see outputs like:**
```
 ✓ SlayStorageStack
   Outputs:
   StorageBucket = slay-storage-123456789012-us-east-1

 ✓ SlayComputeStack
   Outputs:
   ExecutorFunction = slay-executor
```

### Step 6: Use Lambda Backend

```bash
# The StorageBucket output above; needed for values of 1 KB or more
export SLAY_STORAGE_BUCKET="slay-storage-123456789012-us-east-1"
```

```python
import slay

# Set backend to Lambda
slay.set_backend('lambda')

# Now all calls run on AWS Lambda
def process(data):
    return analyze(clean(data))

result = slay.put(process)(my_data).get()
```

## 📚 Documentation by Topic

### Permissions & Authorization
- **[AWS_PERMISSIONS_QUICK_REFERENCE.md](AWS_PERMISSIONS_QUICK_REFERENCE.md)** - List of all required permissions (2-minute read)
- **[AWS_IAM_SETUP.md](AWS_IAM_SETUP.md)** - Detailed IAM setup and troubleshooting (10-minute read)
- **[IAM_POLICY.json](IAM_POLICY.json)** - Ready-to-use IAM policy file

### Deployment
- **[DEPLOYMENT.md](DEPLOYMENT.md)** - Complete deployment guide with troubleshooting
- **[AWS_SETUP_CHECKLIST.md](AWS_SETUP_CHECKLIST.md)** - Step-by-step checklist to verify your setup

### Code
- **[infrastructure/app.py](infrastructure/app.py)** - CDK app entry point
- **[infrastructure/stacks/storage.py](infrastructure/stacks/storage.py)** - S3 and DynamoDB setup
- **[infrastructure/stacks/compute.py](infrastructure/stacks/compute.py)** - Lambda setup

## ⚡ Common Tasks

### Change AWS Region

```bash
export CDK_DEFAULT_REGION="eu-west-1"  # or any other region
cdk deploy
```

### Destroy Infrastructure

```bash
cd infrastructure
cdk destroy
```

### Check Deployment Status

```bash
# View CloudFormation stacks
aws cloudformation list-stacks

# View Lambda functions
aws lambda list-functions

# View S3 buckets
aws s3 ls

# View DynamoDB tables
aws dynamodb list-tables
```

### Monitor CloudWatch Logs

```bash
# View recent logs from Lambda
aws logs tail /aws/lambda/slay-executor --follow

# Or in console: https://console.aws.amazon.com/cloudwatch/
```

## ❌ Troubleshooting

### "User is not authorized"

See **[AWS_IAM_SETUP.md - Troubleshooting](AWS_IAM_SETUP.md#troubleshooting)** for specific permission errors.

### "Unable to resolve AWS account to use"

```bash
# Make sure these are set
echo $CDK_DEFAULT_ACCOUNT
echo $CDK_DEFAULT_REGION

# If empty, set them
export CDK_DEFAULT_ACCOUNT="123456789012"
export CDK_DEFAULT_REGION="us-east-1"
```

### "S3 bucket already exists"

The bucket name includes your account ID and region, so it should be unique. If you get this error:
- Use a different region: `export CDK_DEFAULT_REGION="us-west-2"`
- Or destroy the existing stack: `cdk destroy`

### "Stack creation timeout"

Some deployments take 5-10 minutes. Check progress:
```bash
aws cloudformation describe-stack-events \
  --stack-name SlayStorageStack
```

## 🔒 Security Best Practices

1. **Never commit credentials** to git
2. **Use MFA** for production users (see [AWS_IAM_SETUP.md](AWS_IAM_SETUP.md#security-best-practices))
3. **Use the provided IAM policy** instead of `AdministratorAccess`
4. **Rotate access keys** regularly
5. **Use separate users/profiles** for dev and production

## 💡 Next Steps

After successful setup:

1. **Test with examples**
   ```bash
   source venv/bin/activate
   python examples/01_hello_world.py
   ```

2. **Monitor your deployment**
   - CloudFormation: https://console.aws.amazon.com/cloudformation/
   - Lambda: https://console.aws.amazon.com/lambda/
   - S3: https://s3.console.aws.amazon.com/

3. **Learn more about Slay**
   - Read [README.md](README.md)
   - Explore [examples/](examples/)

4. **Configure auto-scaling** (optional)
   - Edit [infrastructure/stacks/compute.py](infrastructure/stacks/compute.py)
   - Adjust Lambda memory, timeout, ephemeral storage
   - Re-deploy: `cdk deploy`

## 📞 Getting Help

1. **Check the docs** - Most answers are in the links above
2. **Use AWS Support** - https://console.aws.amazon.com/support/
3. **Check CloudWatch Logs** - https://console.aws.amazon.com/cloudwatch/
4. **Review CDK Output** - `cdk deploy` will show detailed error messages

## 🗺️ Setup Overview

```
┌─────────────────────────────────────┐
│ 1. Create AWS Account               │
│    (or use existing)                │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│ 2. Get AWS Credentials              │
│    (Access Key ID + Secret)         │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│ 3. Configure AWS CLI                │
│    (aws configure)                  │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│ 4. Set Up IAM Permissions           │
│    (IAM_POLICY.json)                │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│ 5. Set Environment Variables        │
│    (CDK_DEFAULT_ACCOUNT, etc)       │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│ 6. Deploy with CDK                  │
│    (cdk deploy)                     │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│ 7. Use Lambda Backend in Slay       │
│    (slay.set_backend('lambda'))     │
└─────────────────────────────────────┘
```

---

**Ready to get started?** Begin with **Step 1** above, or jump to the [Quick Start](#-quick-start-5-minutes) if you already have AWS credentials.
