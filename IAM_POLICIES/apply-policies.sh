#!/bin/bash
#
# Apply all Slay IAM policies to an AWS user
#
# Usage:
#   ./apply-policies.sh USERNAME [ACCOUNT_ID]
#
# Example:
#   ./apply-policies.sh slay 026792689263
#

set -e

# Configuration
USERNAME="${1:-slay}"
ACCOUNT_ID="${2:-026792689263}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=================================================="
echo "Slay IAM Policy Application"
echo "=================================================="
echo ""
echo "Username:  $USERNAME"
echo "Account:   $ACCOUNT_ID"
echo "Directory: $SCRIPT_DIR"
echo ""

# Verify AWS CLI is available
if ! command -v aws &> /dev/null; then
    echo "❌ AWS CLI not found. Please install it first:"
    echo "   https://aws.amazon.com/cli/"
    exit 1
fi

# Verify credentials
echo "Verifying AWS credentials..."
if ! aws sts get-caller-identity > /dev/null 2>&1; then
    echo "❌ AWS credentials not configured or invalid"
    echo "   Run: aws configure"
    exit 1
fi

echo "✓ AWS credentials verified"
echo ""

# List of policies to apply
POLICIES=(
    "SlayBootstrap"
    "SlayS3Storage"
    "SlayLambdaCompute"
    "SlayIAMRoles"
    "SlayCloudFormation"
    "SlayECRAssets"
    "SlayDynamoDB"
)

echo "Applying policies..."
echo ""

# Apply each policy
SUCCESS_COUNT=0
FAIL_COUNT=0

for policy_name in "${POLICIES[@]}"; do
    policy_file="$SCRIPT_DIR/${policy_name}.json"
    
    if [ ! -f "$policy_file" ]; then
        echo "⚠️  Skipping $policy_name - file not found"
        ((FAIL_COUNT++))
        continue
    fi
    
    echo -n "  $policy_name... "
    
    if aws iam put-user-policy \
        --user-name "$USERNAME" \
        --policy-name "$policy_name" \
        --policy-document "file://$policy_file" 2>&1 | grep -q "InvalidClientTokenId\|InvalidUserID"; then
        echo "❌ FAILED (credentials invalid or user not found)"
        ((FAIL_COUNT++))
    elif aws iam put-user-policy \
        --user-name "$USERNAME" \
        --policy-name "$policy_name" \
        --policy-document "file://$policy_file" > /dev/null 2>&1; then
        echo "✓ Applied"
        ((SUCCESS_COUNT++))
    else
        echo "❌ FAILED"
        ((FAIL_COUNT++))
    fi
done

echo ""
echo "=================================================="
echo "Results"
echo "=================================================="
echo "✓ Successfully applied: $SUCCESS_COUNT"
echo "❌ Failed: $FAIL_COUNT"
echo ""

if [ $FAIL_COUNT -eq 0 ]; then
    echo "✓ All policies applied successfully!"
    echo ""
    echo "Next steps:"
    echo "  1. Verify policies were applied:"
    echo "     aws iam list-user-policies --user-name $USERNAME"
    echo ""
    echo "  2. Deploy Slay:"
    echo "     cd infrastructure"
    echo "     cdk bootstrap"
    echo "     cdk deploy"
    echo ""
    exit 0
else
    echo "⚠️  Some policies failed to apply"
    echo ""
    echo "Troubleshooting:"
    echo "  - Check AWS credentials: aws sts get-caller-identity"
    echo "  - Verify user exists: aws iam get-user --user-name $USERNAME"
    echo "  - Check error messages above"
    echo ""
    exit 1
fi
