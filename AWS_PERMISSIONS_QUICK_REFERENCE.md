# AWS IAM Permissions Quick Reference for Slay

## TL;DR - What You Need

Use the provided policy file: **`IAM_POLICY.json`**

```bash
# 1. Get your account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# 2. Apply the policy
policy=$(mktemp)
sed "s/ACCOUNT_ID/$ACCOUNT_ID/g" IAM_POLICY.json > "$policy"

# 3. Attach to your user
aws iam put-user-policy \
  --user-name your-username \
  --policy-name SlayDeployment \
  --policy-document "file://$policy"
rm -f "$policy"
```

## What Services Need Permissions

| Service | Why | Min Permissions |
|---------|-----|-----------------|
| **S3** | Data storage | Create/delete buckets, read/write objects |
| **Lambda** | Code execution | Create/delete functions, update code |
| **IAM** | Permissions | Create roles, pass roles to Lambda |
| **DynamoDB** | Metadata | Create/delete tables |
| **CloudFormation** | Deployment | Create/update/delete stacks |
| **ECR** | Container images | Create repos, push images |

## Permissions Needed (By Service)

### S3
```
✓ s3:CreateBucket
✓ s3:DeleteBucket
✓ s3:GetBucketPolicy
✓ s3:PutBucketPolicy
✓ s3:GetBucketVersioning
✓ s3:PutBucketVersioning
✓ s3:GetLifecycleConfiguration
✓ s3:PutLifecycleConfiguration
✓ s3:GetBucketCors
✓ s3:PutBucketCors
✓ s3:GetPublicAccessBlock
✓ s3:PutPublicAccessBlock
✓ s3:GetBucketEncryption
✓ s3:PutBucketEncryption
✓ s3:GetObject
✓ s3:PutObject
✓ s3:DeleteObject
✓ s3:ListBucket
```

### Lambda
```
✓ lambda:CreateFunction
✓ lambda:DeleteFunction
✓ lambda:GetFunction
✓ lambda:GetFunctionConfiguration
✓ lambda:UpdateFunctionCode
✓ lambda:UpdateFunctionConfiguration
✓ lambda:ListFunctions
```

### IAM
```
✓ iam:CreateRole
✓ iam:DeleteRole
✓ iam:GetRole
✓ iam:PassRole
✓ iam:AttachRolePolicy
✓ iam:DetachRolePolicy
✓ iam:PutRolePolicy
✓ iam:DeleteRolePolicy
✓ iam:ListAttachedRolePolicies
```

### DynamoDB
```
✓ dynamodb:CreateTable
✓ dynamodb:DeleteTable
✓ dynamodb:DescribeTable
✓ dynamodb:ListTables
```

### CloudFormation
```
✓ cloudformation:CreateStack
✓ cloudformation:UpdateStack
✓ cloudformation:DeleteStack
✓ cloudformation:DescribeStacks
✓ cloudformation:DescribeStackEvents
✓ cloudformation:DescribeStackResources
✓ cloudformation:ListStacks
✓ cloudformation:GetTemplate
✓ cloudformation:ValidateTemplate
```

### ECR
```
✓ ecr:CreateRepository
✓ ecr:PutImage
✓ ecr:GetDownloadUrlForLayer
✓ ecr:BatchGetImage
✓ ecr:BatchCheckLayerAvailability
```

## Common Errors & Fixes

| Error | Fix |
|-------|-----|
| `iam:PassRole` denied | Add `iam:PassRole` for `slay-*` roles |
| `s3:CreateBucket` denied | Add S3 bucket creation permissions |
| `lambda:CreateFunction` denied | Add Lambda full access or specific permissions |
| `cloudformation:*` denied | Add CloudFormation permissions for `Slay*` stacks |
| `dynamodb:CreateTable` denied | Add DynamoDB table creation permissions |

## AWS Managed Policies (Not Recommended)

If you need quick setup and can't use the custom policy:

| Policy | Use Case |
|--------|----------|
| `AdministratorAccess` | Dev environment only (very permissive) |
| `PowerUserAccess` + `IAMFullAccess` | Moderate permissions |
| `AWSLambdaFullAccess` + `AmazonS3FullAccess` + `CloudFormationFullAccess` | Service-specific |

⚠️ **Not recommended for production** - use `IAM_POLICY.json` instead

## Verify Your Permissions

```bash
# List all your policies
aws iam list-user-policies --user-name your-username

# Check specific policy
aws iam get-user-policy \
  --user-name your-username \
  --policy-name SlayDeployment

# Use policy simulator to test
# https://policysim.aws.amazon.com/
```

## Need More Details?

See **[AWS_IAM_SETUP.md](AWS_IAM_SETUP.md)** for:
- Step-by-step setup instructions
- Troubleshooting guide
- Security best practices
- How to audit and revoke permissions
