# AWS IAM Setup for Slay

This guide explains the AWS IAM permissions needed to deploy and use Slay, and how to set them up.

## Overview

Slay requires permissions to create and manage:
- **S3 buckets** for data storage
- **Lambda functions** for computation
- **DynamoDB tables** for metadata
- **CloudFormation stacks** for infrastructure management
- **IAM roles** for Lambda execution

and, to use it, permissions to invoke the Lambda function and to run the
**Step Functions** state machines.

## Quick Setup

### Option 1: Using Pre-Built Policy (Recommended)

We provide a ready-to-use IAM policy file: `IAM_POLICY.json`

1. **Download the policy file**
   ```bash
   # It's already in the repository
   cat IAM_POLICY.json
   ```

   The policy is too large to be an inline policy of a user: IAM allows
   a user 2,048 characters of inline policy. Create it as a managed
   policy, which may hold 6,144, and attach it.

2. **Create a managed policy in AWS Console**
   - Go to https://console.aws.amazon.com/iam/
   - Click "Policies" → "Create policy"
   - Choose "JSON" editor
   - Copy the contents of `IAM_POLICY.json`
   - Replace `ACCOUNT_ID` with your 12-digit AWS account ID
   - Name the policy `SlayDeployment` and click "Create policy"
   - Click "Users" → Select your user → "Add permissions" →
     "Attach policies directly" → select `SlayDeployment`

3. **Or use AWS CLI**
   ```bash
   # First, get your account ID
   ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
   
   # Replace ACCOUNT_ID in the policy file
   policy=$(mktemp)
   sed "s/ACCOUNT_ID/$ACCOUNT_ID/g" IAM_POLICY.json > "$policy"
   
   # Create the policy and attach it to your user
   aws iam create-policy \
     --policy-name SlayDeployment \
     --policy-document "file://$policy"
   aws iam attach-user-policy \
     --user-name your-username \
     --policy-arn "arn:aws:iam::$ACCOUNT_ID:policy/SlayDeployment"
   rm -f "$policy"
   ```

   If the policy exists already, for example from an older
   `IAM_POLICY.json`, replace its document instead. IAM keeps at most 5
   versions of a policy; delete an old one first if it has 5.
   ```bash
   policy=$(mktemp)
   sed "s/ACCOUNT_ID/$ACCOUNT_ID/g" IAM_POLICY.json > "$policy"
   aws iam create-policy-version \
     --policy-arn "arn:aws:iam::$ACCOUNT_ID:policy/SlayDeployment" \
     --policy-document "file://$policy" \
     --set-as-default
   rm -f "$policy"
   ```

The modular policies in `IAM_POLICIES/` grant the same kinds of rights,
in files small enough to be inline policies of a group. See
[IAM_POLICIES/README.md](IAM_POLICIES/README.md).

### Option 2: Using AWS Managed Policies (Less Secure)

If you want quick access without fine-tuning permissions, attach these managed policies:
- `AdministratorAccess` (⚠️ Not recommended for production)
- Or `PowerUserAccess` + `IAMFullAccess`

```bash
aws iam attach-user-policy \
  --user-name your-username \
  --policy-arn arn:aws:iam::aws:policy/PowerUserAccess

aws iam attach-user-policy \
  --user-name your-username \
  --policy-arn arn:aws:iam::aws:policy/IAMFullAccess
```

## Required Permissions by Service

### S3 (Data Storage)

| Action | Purpose |
|--------|---------|
| `s3:CreateBucket` | Create storage bucket |
| `s3:DeleteBucket` | Clean up resources |
| `s3:PutBucketPolicy` | Configure bucket policies |
| `s3:GetBucketPolicy` | Read bucket policies |
| `s3:PutLifecycleConfiguration` | Set up data retention |
| `s3:PutBucketEncryption` | Enable encryption |
| `s3:GetObject` | Read stored data |
| `s3:PutObject` | Write data to storage |
| `s3:ListBucket` | List bucket contents |

**Resource Pattern:** `arn:aws:s3:::slay-storage-*`

### Lambda (Computation)

| Action | Purpose |
|--------|---------|
| `lambda:CreateFunction` | Deploy Lambda functions |
| `lambda:DeleteFunction` | Clean up resources |
| `lambda:GetFunction` | Read function config |
| `lambda:UpdateFunctionCode` | Update function code |
| `lambda:UpdateFunctionConfiguration` | Modify settings |
| `lambda:InvokeFunction` | Run functions on `slay-executor` |

**Resource Pattern:** `arn:aws:lambda:*:ACCOUNT_ID:function:slay-*`

### Step Functions (Using slay)

| Action | Purpose |
|--------|---------|
| `states:StartExecution` | Run `add_retry` calls and `apply()` |
| `states:DescribeExecution` | Wait for those executions |

**Resource Patterns:**
- `arn:aws:states:*:ACCOUNT_ID:stateMachine:slay-retry` and `slay-fanout`
- `arn:aws:states:*:ACCOUNT_ID:execution:slay-retry:*` and `slay-fanout:*`

### IAM (Permissions)

| Action | Purpose |
|--------|---------|
| `iam:CreateRole` | Create Lambda execution role |
| `iam:DeleteRole` | Clean up roles |
| `iam:PassRole` | Attach role to Lambda |
| `iam:AttachRolePolicy` | Grant permissions to role |
| `iam:PutRolePolicy` | Attach inline policies |

**Resource Patterns:**
- `arn:aws:iam::ACCOUNT_ID:role/slay-*`
- `arn:aws:iam::ACCOUNT_ID:role/SlayStorageStack*`
- `arn:aws:iam::ACCOUNT_ID:role/SlayComputeStack*`

### DynamoDB (Metadata)

| Action | Purpose |
|--------|---------|
| `dynamodb:CreateTable` | Create metadata tables |
| `dynamodb:DeleteTable` | Clean up tables |
| `dynamodb:DescribeTable` | Read table config |

**Resource Pattern:** `arn:aws:dynamodb:*:ACCOUNT_ID:table/slay-*`

### CloudFormation (Infrastructure)

| Action | Purpose |
|--------|---------|
| `cloudformation:CreateStack` | Deploy CDK stacks |
| `cloudformation:UpdateStack` | Update infrastructure |
| `cloudformation:DeleteStack` | Tear down stacks |
| `cloudformation:DescribeStacks` | Check stack status |

**Resource Patterns:**
- `arn:aws:cloudformation:*:ACCOUNT_ID:stack/slay-*`
- `arn:aws:cloudformation:*:ACCOUNT_ID:stack/Slay*`

### ECR (Container Registry)

Required by CDK for packaging Lambda functions:

| Action | Purpose |
|--------|---------|
| `ecr:CreateRepository` | Create image repository |
| `ecr:PutImage` | Push Lambda code images |
| `ecr:BatchCheckLayerAvailability` | Check cached layers |

**Resource Pattern:** `arn:aws:ecr:*:ACCOUNT_ID:repository/slay-*`

## Troubleshooting

### "User is not authorized to perform: iam:PassRole"

**Problem:** Your user doesn't have permission to pass IAM roles to Lambda.

**Solution:** Add this policy:
```json
{
  "Effect": "Allow",
  "Action": "iam:PassRole",
  "Resource": "arn:aws:iam::ACCOUNT_ID:role/slay-*"
}
```

### "User is not authorized to perform: s3:CreateBucket"

**Problem:** Your user can't create S3 buckets.

**Solution:** Ensure your S3 permissions include:
```json
{
  "Effect": "Allow",
  "Action": [
    "s3:CreateBucket",
    "s3:DeleteBucket"
  ],
  "Resource": "arn:aws:s3:::slay-storage-*"
}
```

### "Access Denied: CloudFormation"

**Problem:** Your user can't create CloudFormation stacks.

**Solution:** Add CloudFormation permissions:
```json
{
  "Effect": "Allow",
  "Action": "cloudformation:*",
  "Resource": "arn:aws:cloudformation:*:ACCOUNT_ID:stack/Slay*"
}
```

### "User: arn:aws:iam::... is not authorized to perform: lambda:CreateFunction"

**Problem:** Missing Lambda permissions.

**Solution:** Ensure Lambda permissions are attached:
```bash
aws iam attach-user-policy \
  --user-name your-username \
  --policy-arn arn:aws:iam::aws:policy/AWSLambdaFullAccess
```

## Security Best Practices

1. **Use Least Privilege**
   - Don't use `AdministratorAccess` in production
   - Use the provided `IAM_POLICY.json` which is scoped to slay resources

2. **Use Resource-Specific Policies**
   - All permissions in `IAM_POLICY.json` target `slay-*` resources
   - Prevents accidental modification of other resources

3. **Use MFA for Production Users**
   ```bash
   # Enable MFA on your AWS user
   aws iam enable-mfa-device \
     --user-name your-username \
     --serial-number arn:aws:iam::ACCOUNT_ID:mfa/your-username \
     --authentication-code1 123456 \
     --authentication-code2 789012
   ```

4. **Audit Permissions Regularly**
   ```bash
   # Check which policies a user has
   aws iam list-attached-user-policies --user-name your-username

   # Read the default version of the policy
   aws iam get-policy-version \
     --policy-arn "arn:aws:iam::ACCOUNT_ID:policy/SlayDeployment" \
     --version-id "$(aws iam get-policy \
       --policy-arn "arn:aws:iam::ACCOUNT_ID:policy/SlayDeployment" \
       --query Policy.DefaultVersionId --output text)"
   ```

5. **Use Separate Users for Different Environments**
   - One user for development (more permissive)
   - One user for production (minimal permissions)

## Verifying Your Setup

Once permissions are configured, verify:

```bash
# Test S3 access
aws s3 ls

# Test Lambda access
aws lambda list-functions

# Test IAM access
aws iam list-roles | grep slay

# Test CloudFormation access
aws cloudformation list-stacks

# Finally, try deploying
cd infrastructure
cdk deploy
```

## Revoking Access

To remove Slay permissions from a user:

```bash
# Using CLI
aws iam detach-user-policy \
  --user-name your-username \
  --policy-arn "arn:aws:iam::ACCOUNT_ID:policy/SlayDeployment"

# Delete the policy once nothing has it attached and only its default
# version is left (aws iam delete-policy-version removes the others)
aws iam delete-policy \
  --policy-arn "arn:aws:iam::ACCOUNT_ID:policy/SlayDeployment"

# Or in AWS Console:
# 1. Go to IAM → Users
# 2. Select user
# 3. Select the policy under "Permissions policies" and click "Remove"
```

## Additional Resources

- [AWS IAM Documentation](https://docs.aws.amazon.com/iam/)
- [AWS IAM Best Practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)
- [AWS Policy Simulator](https://policysim.aws.amazon.com/) - Test your policies
- [IAM Policy Examples](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_examples.html)

## Need Help?

1. Check [AWS IAM Troubleshooting Guide](https://docs.aws.amazon.com/IAM/latest/UserGuide/troubleshoot_access.html)
2. Use the [AWS Policy Simulator](https://policysim.aws.amazon.com/) to test your policies
3. Check CloudFormation events for detailed error messages:
   ```bash
   aws cloudformation describe-stack-events \
     --stack-name SlayStorageStack \
     --region us-east-1
   ```
