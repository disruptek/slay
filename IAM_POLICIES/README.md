# Slay IAM Policies

This directory contains separate, modular IAM policies for deploying and using Slay on AWS, plus a comprehensive management script.

## 🚀 Quick Start

Use the management script to handle all policies. It puts them on an IAM
group as inline policies; add your users to the group.

```bash
# Apply all policies to the 'slayers' group
./manage-policies.sh apply -t slayers

# Verify they're all applied
./manage-policies.sh verify -t slayers

# Check detailed status
./manage-policies.sh status -t slayers
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

# Apply only SlayClient, for a group that uses slay but does not deploy it
./manage-policies.sh apply -t slay-users --client

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
- `-u, --user NAME` - Same as `-t NAME --type user`
- `--client` - Work on SlayClient only: `apply`, `remove`, `verify` and
  `status` then ignore the deployment policies
- `-d, --dry-run` - Preview changes without applying
- `-v, --verbose` - Show detailed output
- `-r, --retries N` - Number of retries (default: 3)
- `--delay N` - Delay between retries in seconds (default: 2)

**Examples:**
```bash
# Apply to slayers group (default)
./manage-policies.sh apply -t slayers

# Give one user the client rights only
./manage-policies.sh apply -u alice --client

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

| Policy | Characters | Purpose |
|--------|-----------:|---------|
| **SlayS3Storage.json** | 707 | S3 bucket management |
| **SlayLambdaCompute.json** | 415 | Lambda function management |
| **SlayIAMRoles.json** | 765 | IAM role management + STS assume |
| **SlayCloudFormation.json** | 861 | CloudFormation stack management |
| **SlayDynamoDB.json** | 331 | DynamoDB table management |
| **SlayECRAssets.json** | 620 | ECR repository + CDK asset buckets |
| **SlayBootstrap.json** | 243 | CDK bootstrap SSM parameters |
| **SlayClient.json** | 857 | Use slay: invoke the executor, run the state machines, read and write the bucket |

**Total:** 4,799 characters across 8 policies.

**Limits:** IAM adds up all the inline policies of one user, group or
role, and does not count whitespace. A group may hold 5,120 characters
and a user 2,048. Put all eight policies on a group. A user can hold
SlayClient alone, which is what `--client` applies.

## Quick Setup

### Option 1: Apply All Policies via AWS CLI

```bash
#!/bin/bash

GROUP="slayers"

# Apply each policy
for policy in Slay*.json; do
    policy_name="${policy%.json}"
    echo "Applying $policy_name..."
    aws iam put-group-policy \
        --group-name "$GROUP" \
        --policy-name "$policy_name" \
        --policy-document "file://$(pwd)/$policy"
done

echo "✓ All policies applied!"
```

### Option 2: Apply Policies via AWS Console

1. Go to https://console.aws.amazon.com/iam/home#/groups and open the `slayers` group
2. For each policy file:
   - Click "Add permissions" → "Create inline policy"
   - Choose "JSON" tab
   - Copy the contents of the policy file
   - Set the policy name (e.g., "SlayS3Storage")
   - Click "Create policy"

### Option 3: Apply a Single Policy

If you only need specific functionality:

```bash
aws iam put-group-policy \
    --group-name slayers \
    --policy-name SlayClient \
    --policy-document file://SlayClient.json
```

## What Each Policy Does

Apply any one of them with the command of Option 3, with its name.

### SlayS3Storage.json
**Permissions:** S3 bucket and object management
**Used for:** Creating/managing Slay data storage bucket
**Minimum required:** Yes, to deploy

### SlayLambdaCompute.json
**Permissions:** Lambda function lifecycle management
**Used for:** Creating and configuring Lambda executor function
**Minimum required:** Yes, to deploy

### SlayIAMRoles.json
**Permissions:** IAM role creation, policy attachment, STS assume role
**Used for:** Creating execution roles for Lambda and CDK
**Minimum required:** Yes, to deploy

### SlayCloudFormation.json
**Permissions:** CloudFormation stack and changeset management
**Used for:** Deploying CDK infrastructure
**Minimum required:** Yes, to deploy

### SlayDynamoDB.json
**Permissions:** DynamoDB table management
**Used for:** Creating metadata tables (optional)
**Minimum required:** No (only if using DynamoDB)

### SlayECRAssets.json
**Permissions:** ECR repository + CDK asset bucket management
**Used for:** Storing container images and deployment artifacts
**Minimum required:** Yes, to deploy (for CDK asset uploads)

### SlayBootstrap.json
**Permissions:** SSM parameter access for CDK bootstrap
**Used for:** Reading/writing CDK bootstrap configuration
**Minimum required:** Yes, to deploy

### SlayClient.json
**Permissions:** `lambda:GetFunction` and `lambda:InvokeFunction` on
`slay-executor`; `states:StartExecution` on `slay-retry` and
`slay-fanout`, and `states:DescribeExecution` on their executions;
`s3:GetObject`, `s3:PutObject` and `s3:ListBucket` on the storage bucket
**Used for:** Running functions on Lambda: `run()`, `add_retry`,
`apply()`, jobs and the result cache (see "Permissions" in
[../README.md](../README.md))
**Minimum required:** Yes, to use slay

## Minimum Setup (Bare Essentials)

To deploy Slay, you need at minimum:

1. **SlayS3Storage.json** - Data storage
2. **SlayLambdaCompute.json** - Compute
3. **SlayIAMRoles.json** - Permissions
4. **SlayCloudFormation.json** - Deployment
5. **SlayECRAssets.json** - Assets
6. **SlayBootstrap.json** - Bootstrap

To use it, you need **SlayClient.json**.

```bash
for policy in SlayS3Storage SlayLambdaCompute SlayIAMRoles SlayCloudFormation SlayECRAssets SlayBootstrap SlayClient; do
    aws iam put-group-policy \
        --group-name slayers \
        --policy-name "$policy" \
        --policy-document "file://${policy}.json"
done
```

## Optional Setup

Add if needed:

- **SlayDynamoDB.json** - Only if using DynamoDB for metadata

## Verification

```bash
# List all policies for the group
aws iam list-group-policies --group-name slayers

# View a specific policy
aws iam get-group-policy --group-name slayers --policy-name SlayS3Storage

# Verify permissions with policy simulator
# https://policysim.aws.amazon.com/
```

## Troubleshooting

### Policy size exceeded

If you get "LimitExceeded: Maximum policy size ... exceeded", the inline
policies of that user or group add up to more than its limit (see
[Policies](#policies)).

**Solution:** Apply the policies to a group, or give a user SlayClient
only (`--client`).

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
2. **Use separate groups** - Users who only run slay need SlayClient, not the deployment policies
3. **Rotate credentials** - Periodically create new access keys and revoke old ones
4. **Monitor usage** - Check CloudTrail for unauthorized access attempts
5. **Principle of least privilege** - Only grant permissions that are necessary

## Cleanup

To remove all Slay policies:

```bash
for policy in SlayS3Storage SlayLambdaCompute SlayIAMRoles SlayCloudFormation SlayDynamoDB SlayECRAssets SlayBootstrap SlayClient; do
    aws iam delete-group-policy \
        --group-name slayers \
        --policy-name "$policy"
done
```

## Need Help?

- Check [../DEPLOYMENT.md](../DEPLOYMENT.md) for deployment instructions
- Review [../AWS_IAM_SETUP.md](../AWS_IAM_SETUP.md) for detailed IAM information
- Use AWS Policy Simulator: https://policysim.aws.amazon.com/
