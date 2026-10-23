# Slay IAM Policies

This directory contains separate, modular IAM policies for deploying and using Slay on AWS, plus a comprehensive management script.

## 🚀 Quick Start

Use the management script to handle all policies:

```bash
# Apply all remaining policies
./manage-policies.sh apply -u slay

# Verify they're all applied
./manage-policies.sh verify -u slay

# Check detailed status
./manage-policies.sh status -u slay
```

Then deploy:
```bash
cd ../infrastructure
cdk deploy --require-approval=never
```

See [Usage](#usage) below for more commands.

## Usage

### Using the Management Script (Recommended)

The `manage-policies.sh` script handles all policy operations for groups or users:

```bash
# Apply all policies to 'slayers' group (RECOMMENDED)
./manage-policies.sh apply -t slayers

# Verify all required policies are applied
./manage-policies.sh verify -t slayers

# Show detailed status
./manage-policies.sh status -t slayers

# List applied policies
./manage-policies.sh list -t slayers

# Remove all policies
./manage-policies.sh remove -t slayers

# Validate policy files
./manage-policies.sh validate

# Help
./manage-policies.sh help
```

**Options:**
- `-t, --target NAME` - Group or user name (required for most commands)
- `--type TYPE` - Target type: "group" (default) or "user"
- `-d, --dry-run` - Preview changes without applying
- `-v, --verbose` - Show detailed output
- `-r, --retries N` - Number of retries (default: 3)
- `--delay N` - Delay between retries in seconds (default: 2)

**Examples:**
```bash
# Apply to slayers group (default)
./manage-policies.sh apply -t slayers

# Apply to a specific user instead
./manage-policies.sh apply -t slay --type user

# Apply with verbose output
./manage-policies.sh apply -t slayers -v

# Dry-run to see what would happen
./manage-policies.sh apply -t slayers --dry-run

# Apply with custom retry settings
./manage-policies.sh apply -t slayers -r 5 --delay 3

# Verify and show detailed status
./manage-policies.sh verify -t slayers
./manage-policies.sh status -t slayers
```

## Policies

| Policy | Size | Limit Status | Purpose |
|--------|------|--------------|---------|
| **SlayS3Storage.json** | 1,039 B | ✓ OK | S3 bucket management |
| **SlayLambdaCompute.json** | 583 B | ✓ OK | Lambda function management |
| **SlayIAMRoles.json** | 1,119 B | ✓ OK | IAM role management + STS assume |
| **SlayCloudFormation.json** | 1,141 B | ✓ OK | CloudFormation stack management |
| **SlayDynamoDB.json** | 472 B | ✓ OK | DynamoDB table management |
| **SlayECRAssets.json** | 916 B | ✓ OK | ECR repository + CDK asset buckets |
| **SlayBootstrap.json** | 357 B | ✓ OK | CDK bootstrap SSM parameters |

**Total:** 5,627 bytes across 7 policies
**AWS Limit:** 2,048 bytes per inline policy ✓ (all policies compliant)
**Note:** Each policy is a separate inline policy to stay within AWS's 2048 byte limit per policy

## Quick Setup

### Option 1: Apply All Policies via AWS CLI

```bash
#!/bin/bash

# Your AWS account ID
ACCOUNT_ID="026792689263"
USERNAME="slay"

# Apply each policy
for policy in Slay*.json; do
    policy_name="${policy%.json}"
    echo "Applying $policy_name..."
    aws iam put-user-policy \
        --user-name "$USERNAME" \
        --policy-name "$policy_name" \
        --policy-document "file://$(pwd)/$policy"
done

echo "✓ All policies applied!"
```

### Option 2: Apply Policies via AWS Console

1. Go to https://console.aws.amazon.com/iam/users/slay
2. For each policy file:
   - Click "Add inline policy"
   - Choose "JSON" tab
   - Copy the contents of the policy file
   - Set the policy name (e.g., "SlayS3Storage")
   - Click "Create policy"

### Option 3: Apply a Single Policy

If you only need specific functionality:

```bash
aws iam put-user-policy \
    --user-name slay \
    --policy-name SlayS3Storage \
    --policy-document file://SlayS3Storage.json
```

## What Each Policy Does

### SlayS3Storage.json
**Permissions:** S3 bucket and object management
**Used for:** Creating/managing Slay data storage bucket
**Minimum required:** Yes

```bash
aws iam put-user-policy \
    --user-name slay \
    --policy-name SlayS3Storage \
    --policy-document file://SlayS3Storage.json
```

### SlayLambdaCompute.json
**Permissions:** Lambda function lifecycle management
**Used for:** Creating and configuring Lambda executor function
**Minimum required:** Yes

```bash
aws iam put-user-policy \
    --user-name slay \
    --policy-name SlayLambdaCompute \
    --policy-document file://SlayLambdaCompute.json
```

### SlayIAMRoles.json
**Permissions:** IAM role creation, policy attachment, STS assume role
**Used for:** Creating execution roles for Lambda and CDK
**Minimum required:** Yes

```bash
aws iam put-user-policy \
    --user-name slay \
    --policy-name SlayIAMRoles \
    --policy-document file://SlayIAMRoles.json
```

### SlayCloudFormation.json
**Permissions:** CloudFormation stack and changeset management
**Used for:** Deploying CDK infrastructure
**Minimum required:** Yes

```bash
aws iam put-user-policy \
    --user-name slay \
    --policy-name SlayCloudFormation \
    --policy-document file://SlayCloudFormation.json
```

### SlayDynamoDB.json
**Permissions:** DynamoDB table management
**Used for:** Creating metadata tables (optional)
**Minimum required:** No (only if using DynamoDB)

```bash
aws iam put-user-policy \
    --user-name slay \
    --policy-name SlayDynamoDB \
    --policy-document file://SlayDynamoDB.json
```

### SlayECRAssets.json
**Permissions:** ECR repository + CDK asset bucket management
**Used for:** Storing container images and deployment artifacts
**Minimum required:** Yes (for CDK asset uploads)

```bash
aws iam put-user-policy \
    --user-name slay \
    --policy-name SlayECRAssets \
    --policy-document file://SlayECRAssets.json
```

### SlayBootstrap.json
**Permissions:** SSM parameter access for CDK bootstrap
**Used for:** Reading/writing CDK bootstrap configuration
**Minimum required:** Yes

```bash
aws iam put-user-policy \
    --user-name slay \
    --policy-name SlayBootstrap \
    --policy-document file://SlayBootstrap.json
```

## Minimum Setup (Bare Essentials)

To deploy Slay, you need at minimum:

1. **SlayS3Storage.json** - Data storage
2. **SlayLambdaCompute.json** - Compute
3. **SlayIAMRoles.json** - Permissions
4. **SlayCloudFormation.json** - Deployment
5. **SlayECRAssets.json** - Assets
6. **SlayBootstrap.json** - Bootstrap

```bash
for policy in SlayS3Storage SlayLambdaCompute SlayIAMRoles SlayCloudFormation SlayECRAssets SlayBootstrap; do
    aws iam put-user-policy \
        --user-name slay \
        --policy-name "$policy" \
        --policy-document "file://${policy}.json"
done
```

## Optional Setup

Add if needed:

- **SlayDynamoDB.json** - Only if using DynamoDB for metadata

## Verification

```bash
# List all policies for the user
aws iam list-user-policies --user-name slay

# View a specific policy
aws iam get-user-policy --user-name slay --policy-name SlayS3Storage

# Verify permissions with policy simulator
# https://policysim.aws.amazon.com/
```

## Troubleshooting

### Policy size exceeded

If you get "LimitExceeded: Inline policy size limit exceeded", you have:
- Too many inline policies
- Individual policy too large

**Solution:** Combine related policies or use managed policies

### Missing permissions

If deployment fails with "not authorized to perform":
1. Identify the missing action from error message
2. Find which policy file contains it
3. Ensure that policy is applied
4. Check Resource ARN matches (may need to update account ID)

### Permission denied on specific action

Check if the Resource ARN matches:
- Account ID: `026792689263` (should be in all policies)
- Resource pattern: Should match what you're creating (e.g., `slay-*`)

## Customization

All policies use account ID `026792689263`. To use in another account:

```bash
# Replace account ID throughout all policies
sed -i 's/026792689263/YOUR_ACCOUNT_ID/g' *.json

# Then apply as normal
for policy in Slay*.json; do
    ...
done
```

## Best Practices

1. **Apply only needed policies** - Don't apply SlayDynamoDB if you're not using DynamoDB
2. **Use separate profiles** - Create separate IAM users for dev vs production
3. **Rotate credentials** - Periodically create new access keys and revoke old ones
4. **Monitor usage** - Check CloudTrail for unauthorized access attempts
5. **Principle of least privilege** - Only grant permissions that are necessary

## Cleanup

To remove all Slay policies:

```bash
for policy in SlayS3Storage SlayLambdaCompute SlayIAMRoles SlayCloudFormation SlayDynamoDB SlayECRAssets SlayBootstrap; do
    aws iam delete-user-policy \
        --user-name slay \
        --policy-name "$policy"
done
```

## Need Help?

- Check [../DEPLOYMENT.md](../DEPLOYMENT.md) for deployment instructions
- Review [../AWS_IAM_SETUP.md](../AWS_IAM_SETUP.md) for detailed IAM information
- Use AWS Policy Simulator: https://policysim.aws.amazon.com/
