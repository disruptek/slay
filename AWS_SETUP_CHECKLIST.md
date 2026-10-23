# AWS Setup Checklist for Slay Deployment

Complete this checklist to ensure your AWS account is properly configured for Slay deployment.

## Prerequisites ✓

- [ ] AWS Account created (https://aws.amazon.com/)
- [ ] AWS CLI installed (`aws --version` shows v2+)
- [ ] Python 3.11+ installed (`python --version`)
- [ ] Node.js installed (`node --version`)
- [ ] Git installed (`git --version`)

## AWS Credentials ✓

- [ ] AWS Access Key ID obtained
  - Go to https://console.aws.amazon.com/iam/
  - Click "Users" → Select your user
  - "Security credentials" tab → "Create access key"
  
- [ ] AWS Secret Access Key obtained (saved safely)

- [ ] Credentials configured
  ```bash
  aws configure
  # OR
  export AWS_ACCESS_KEY_ID="your-key"
  export AWS_SECRET_ACCESS_KEY="your-secret"
  export AWS_DEFAULT_REGION="us-east-1"
  ```

- [ ] Credentials verified
  ```bash
  aws sts get-caller-identity
  # Should show your account ID, User ID, and ARN
  ```

## IAM Permissions ✓

- [ ] Account ID obtained
  ```bash
  ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
  echo $ACCOUNT_ID
  ```

- [ ] IAM policy applied
  ```bash
  # Option A: Using the provided policy file
  policy=$(mktemp)
  sed "s/ACCOUNT_ID/$ACCOUNT_ID/g" IAM_POLICY.json > "$policy"
  aws iam put-user-policy \
    --user-name your-username \
    --policy-name SlayDeployment \
    --policy-document "file://$policy"
  rm -f "$policy"
  
  # Option B: Using AWS managed policies
  aws iam attach-user-policy \
    --user-name your-username \
    --policy-arn arn:aws:iam::aws:policy/PowerUserAccess
  ```

- [ ] Permissions verified
  ```bash
  # Test S3 access
  aws s3 ls
  
  # Test Lambda access
  aws lambda list-functions
  
  # Test IAM access
  aws iam list-roles
  
  # Test CloudFormation access
  aws cloudformation list-stacks
  ```

## AWS Region ✓

- [ ] Preferred region selected
  ```bash
  # Available regions: us-east-1, us-west-2, eu-west-1, etc.
  export AWS_DEFAULT_REGION="us-east-1"
  aws ec2 describe-regions  # List all available regions
  ```

- [ ] Region set in environment
  ```bash
  export CDK_DEFAULT_REGION="us-east-1"
  ```

## Slay Installation ✓

- [ ] Slay package installed
  ```bash
  cd /path/to/slay
  source venv/bin/activate  # Activate virtual environment
  pip install -e .
  python -c "import slay; print(slay.__version__)"
  ```

- [ ] CDK dependencies installed
  ```bash
  cd infrastructure
  pip install -r requirements.txt
  npm install -g aws-cdk
  cdk --version
  ```

- [ ] S3 bucket doesn't already exist
  ```bash
  # The deployment will create: slay-storage-{ACCOUNT_ID}-{REGION}
  # Check if it already exists
  aws s3 ls s3://slay-storage-$ACCOUNT_ID-$AWS_DEFAULT_REGION
  # Should return "NoSuchBucket" error (this is expected)
  ```

## Deployment Preparation ✓

- [ ] All environment variables set
  ```bash
  # Verify
  echo "Account: $CDK_DEFAULT_ACCOUNT"
  echo "Region: $CDK_DEFAULT_REGION"
  echo "Backend: $SLAY_BACKEND"
  ```

- [ ] No existing Slay infrastructure
  ```bash
  # Check for existing CloudFormation stacks
  aws cloudformation list-stacks \
    --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE \
    --region $CDK_DEFAULT_REGION | grep -i slay
  # Should return empty
  ```

- [ ] Sufficient AWS account limits
  ```bash
  # Default limits are usually sufficient:
  # - Lambda: 1000 concurrent
  # - S3: Unlimited buckets
  # - DynamoDB: Tables on-demand
  # Check limits: https://console.aws.amazon.com/servicequotas/
  ```

## Ready to Deploy ✓

- [ ] All previous items completed

- [ ] Review deployment plan
  ```bash
  cd infrastructure
  cdk diff
  # Review what will be created
  ```

- [ ] Deploy
  ```bash
  cdk deploy --require-approval=never
  # Or with approval prompt
  cdk deploy
  ```

- [ ] Deployment successful
  ```bash
  # Check outputs
  aws cloudformation describe-stacks \
    --stack-name SlayStorageStack \
    --query 'Stacks[0].Outputs'
  
  aws cloudformation describe-stacks \
    --stack-name SlayComputeStack \
    --query 'Stacks[0].Outputs'
  ```

## Post-Deployment ✓

- [ ] S3 bucket created and accessible
  ```bash
  aws s3 ls s3://slay-storage-$ACCOUNT_ID-$AWS_DEFAULT_REGION
  ```

- [ ] Lambda function created
  ```bash
  aws lambda get-function --function-name slay-executor
  ```

- [ ] DynamoDB table created
  ```bash
  aws dynamodb list-tables
  # Should show a table starting with "slay-"
  ```

- [ ] Set Lambda backend in code
  ```python
  import slay
  slay.set_backend('lambda')
  ```

- [ ] Test remote execution
  ```bash
  python examples/01_hello_world.py
  ```

## Cleanup (When Done) ✓

- [ ] Remove resources when no longer needed
  ```bash
  cd infrastructure
  cdk destroy --force
  ```

- [ ] Remove IAM policy
  ```bash
  aws iam delete-user-policy \
    --user-name your-username \
    --policy-name SlayDeployment
  ```

- [ ] Revoke access keys (if no longer needed)
  ```bash
  aws iam delete-access-key \
    --user-name your-username \
    --access-key-id YOUR_ACCESS_KEY_ID
  ```

## Troubleshooting

### Issue: "Unable to resolve AWS account to use"
- [ ] Verify `CDK_DEFAULT_ACCOUNT` is set: `echo $CDK_DEFAULT_ACCOUNT`
- [ ] Verify `CDK_DEFAULT_REGION` is set: `echo $CDK_DEFAULT_REGION`
- [ ] See [DEPLOYMENT.md](DEPLOYMENT.md#error-unable-to-resolve-aws-account-to-use)

### Issue: "User is not authorized"
- [ ] Check permissions: `aws iam get-user-policy --user-name your-username --policy-name SlayDeployment`
- [ ] See [AWS_IAM_SETUP.md](AWS_IAM_SETUP.md)

### Issue: "S3 bucket already exists"
- [ ] Use different region: `export CDK_DEFAULT_REGION="us-west-2"`
- [ ] Or destroy existing stack: `cdk destroy`

### Issue: CDK Command Not Found
- [ ] Install CDK globally: `npm install -g aws-cdk`
- [ ] Verify: `cdk --version`
- [ ] Add to PATH if needed: `export PATH="/path/to/node_modules/.bin:$PATH"`

### Issue: CloudFormation Stack Creation Timeout
- [ ] Check CloudFormation events: `aws cloudformation describe-stack-events --stack-name Slay*`
- [ ] May need to wait (can take 5-10 minutes)
- [ ] Check CloudWatch logs for Lambda function issues

## Getting Help

1. **Check documentation**
   - [DEPLOYMENT.md](DEPLOYMENT.md) - Deployment guide
   - [AWS_IAM_SETUP.md](AWS_IAM_SETUP.md) - Permissions guide
   - [AWS_PERMISSIONS_QUICK_REFERENCE.md](AWS_PERMISSIONS_QUICK_REFERENCE.md) - Quick reference

2. **Check AWS resources**
   - [AWS CDK Documentation](https://docs.aws.amazon.com/cdk/)
   - [AWS IAM Troubleshooting](https://docs.aws.amazon.com/IAM/latest/UserGuide/troubleshoot_access.html)
   - [AWS CLI Reference](https://docs.aws.amazon.com/cli/latest/reference/)

3. **Verify setup**
   ```bash
   # Run this diagnostic script
   echo "=== AWS Setup Diagnostic ==="
   echo "AWS CLI Version:"
   aws --version
   echo ""
   echo "Account ID:"
   aws sts get-caller-identity
   echo ""
   echo "Current Credentials:"
   aws sts get-caller-identity
   echo ""
   echo "S3 Buckets:"
   aws s3 ls
   echo ""
   echo "Lambda Functions:"
   aws lambda list-functions
   echo ""
   echo "CloudFormation Stacks:"
   aws cloudformation list-stacks
   ```

## Status Summary

- Prerequisites: _____/4 completed
- AWS Credentials: _____/3 completed
- IAM Permissions: _____/3 completed
- AWS Region: _____/2 completed
- Slay Installation: _____/4 completed
- Deployment Prep: _____/3 completed
- Ready to Deploy: _____/4 completed
- Post-Deployment: _____/5 completed

**Total Progress: _____ / 34 items**
