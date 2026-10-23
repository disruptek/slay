#!/bin/bash
#
# Comprehensive IAM Policy Manager for Slay
#
# Features:
#   - Apply/remove/list/verify policies
#   - Batch operations
#   - Error handling and retries
#   - Color output
#   - Dry-run mode
#   - Policy validation
#   - Progress tracking
#

set -o pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLICIES=(
    "SlayBootstrap"
    "SlayS3Storage"
    "SlayLambdaCompute"
    "SlayIAMRoles"
    "SlayCloudFormation"
    "SlayECRAssets"
    "SlayDynamoDB"
)
REQUIRED_POLICIES=(
    "SlayBootstrap"
    "SlayS3Storage"
    "SlayLambdaCompute"
    "SlayIAMRoles"
    "SlayCloudFormation"
    "SlayECRAssets"
)

# Defaults
TARGET_TYPE="group"  # "user" or "group"
TARGET_NAME=""
ACCOUNT_ID=""
DRY_RUN=0
VERBOSE=0
RETRY_COUNT=3
RETRY_DELAY=2

# Usage
usage() {
    cat << EOF
Usage: $0 <command> [options]

Commands:
  apply       Apply all policies to group/user
  remove      Remove all policies from group/user
  list        List all applied policies
  verify      Verify all required policies are applied
  status      Show detailed status
  validate    Validate policy files
  help        Show this help message

Options:
  -t, --target TARGET_NAME    Group or user name (required for apply/remove/list/verify)
  --type TYPE                 Target type: "group" (default) or "user"
  -a, --account ACCOUNT_ID    AWS account ID (default: from AWS CLI)
  -d, --dry-run               Show what would be done without doing it
  -v, --verbose               Verbose output
  -r, --retries N             Number of retries on failure (default: $RETRY_COUNT)
  --delay N                   Delay between retries in seconds (default: $RETRY_DELAY)

Examples:
  # Apply all policies to 'slayers' group (recommended)
  $0 apply -t slayers

  # Apply to a specific user
  $0 apply -t slay --type user

  # Apply with dry-run to see what happens
  $0 apply -t slayers --dry-run

  # Verify group has all required policies
  $0 verify -t slayers

  # Remove all policies
  $0 remove -t slayers

  # Show detailed status
  $0 status -t slayers

  # Validate policy files
  $0 validate

EOF
    exit 0
}

# Helper functions
log_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

log_success() {
    echo -e "${GREEN}✓${NC} $1"
}

log_error() {
    echo -e "${RED}✗${NC} $1" >&2
}

log_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

log_verbose() {
    if [ $VERBOSE -eq 1 ]; then
        echo -e "${BLUE}→${NC} $1"
    fi
}

# Check if AWS CLI is available
check_aws_cli() {
    if ! command -v aws &> /dev/null; then
        log_error "AWS CLI not found. Install from: https://aws.amazon.com/cli/"
        exit 1
    fi
    log_verbose "AWS CLI found: $(aws --version)"
}

# Verify AWS credentials are configured
check_credentials() {
    if ! aws sts get-caller-identity > /dev/null 2>&1; then
        log_error "AWS credentials not configured or invalid"
        log_info "Run: aws configure"
        exit 1
    fi
    log_verbose "AWS credentials verified"
}

# Get account ID from AWS CLI if not provided
get_account_id() {
    if [ -z "$ACCOUNT_ID" ]; then
        ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null)
        if [ -z "$ACCOUNT_ID" ]; then
            log_error "Failed to get account ID from AWS CLI"
            exit 1
        fi
        log_verbose "Account ID from AWS: $ACCOUNT_ID"
    fi
}

# Validate policy files exist and are valid JSON
validate_policies() {
    log_info "Validating policy files..."
    
    local invalid=0
    local max_size=2048
    
    for policy in "${POLICIES[@]}"; do
        local file="$SCRIPT_DIR/${policy}.json"
        
        if [ ! -f "$file" ]; then
            log_error "Policy file not found: $file"
            ((invalid++))
            continue
        fi
        
        if ! jq empty "$file" 2>/dev/null; then
            log_error "Policy file has invalid JSON: $file"
            ((invalid++))
            continue
        fi
        
        # Check file size (AWS limit is 2048 bytes per inline policy)
        local size=$(wc -c < "$file")
        if [ "$size" -gt "$max_size" ]; then
            log_error "Policy file exceeds 2048 byte limit: $policy ($size bytes)"
            ((invalid++))
            continue
        fi
        
        log_verbose "✓ $policy ($size bytes)"
    done
    
    if [ $invalid -gt 0 ]; then
        log_error "$invalid policy file(s) are invalid"
        exit 1
    fi
    
    log_success "All policy files are valid (within 2048 byte limit)"
}

# Check if target (user or group) exists
check_target_exists() {
    if [ "$TARGET_TYPE" = "group" ]; then
        if ! aws iam get-group --group-name "$TARGET_NAME" > /dev/null 2>&1; then
            log_error "Group '$TARGET_NAME' not found"
            log_info "Available groups:"
            aws iam list-groups --query 'Groups[].GroupName' --output text | tr '\t' '\n' | sed 's/^/  /'
            exit 1
        fi
        log_verbose "Group '$TARGET_NAME' exists"
    else
        if ! aws iam get-user --user-name "$TARGET_NAME" > /dev/null 2>&1; then
            log_error "User '$TARGET_NAME' not found"
            log_info "Available users:"
            aws iam list-users --query 'Users[].UserName' --output text | tr '\t' '\n' | sed 's/^/  /'
            exit 1
        fi
        log_verbose "User '$TARGET_NAME' exists"
    fi
}

# Apply a single policy with retry logic
apply_policy() {
    local policy_name=$1
    local policy_file="$SCRIPT_DIR/${policy_name}.json"
    local attempt=1
    
    if [ ! -f "$policy_file" ]; then
        log_error "Policy file not found: $policy_file"
        return 1
    fi
    
    while [ $attempt -le $RETRY_COUNT ]; do
        if [ $DRY_RUN -eq 1 ]; then
            echo "[DRY-RUN] Would apply to $TARGET_TYPE '$TARGET_NAME': $policy_name"
            return 0
        fi
        
        log_verbose "Applying $policy_name to $TARGET_TYPE '$TARGET_NAME' (attempt $attempt/$RETRY_COUNT)..."
        
        local cmd_args=(
            "--policy-name" "$policy_name"
            "--policy-document" "file://$policy_file"
        )
        
        if [ "$TARGET_TYPE" = "group" ]; then
            cmd_args+=("--group-name" "$TARGET_NAME")
        else
            cmd_args+=("--user-name" "$TARGET_NAME")
        fi
        
        if aws iam put-"${TARGET_TYPE}"-policy "${cmd_args[@]}" 2>&1 | grep -q "InvalidClientTokenId\|ExpiredToken"; then
            log_error "AWS credentials expired"
            return 1
        fi
        
        # Verify policy was applied
        if [ "$TARGET_TYPE" = "group" ]; then
            aws iam get-group-policy --group-name "$TARGET_NAME" --policy-name "$policy_name" > /dev/null 2>&1 && return 0
        else
            aws iam get-user-policy --user-name "$TARGET_NAME" --policy-name "$policy_name" > /dev/null 2>&1 && return 0
        fi
        
        if [ $attempt -lt $RETRY_COUNT ]; then
            log_warning "Retrying in ${RETRY_DELAY}s..."
            sleep "$RETRY_DELAY"
        fi
        
        ((attempt++))
    done
    
    return 1
}

# Remove a single policy
remove_policy() {
    local policy_name=$1
    
    if [ $DRY_RUN -eq 1 ]; then
        echo "[DRY-RUN] Would remove from $TARGET_TYPE '$TARGET_NAME': $policy_name"
        return 0
    fi
    
    log_verbose "Removing $policy_name from $TARGET_TYPE '$TARGET_NAME'..."
    
    if [ "$TARGET_TYPE" = "group" ]; then
        aws iam delete-group-policy \
            --group-name "$TARGET_NAME" \
            --policy-name "$policy_name" 2>&1 | grep -q "InvalidClientTokenId" && return 1
    else
        aws iam delete-user-policy \
            --user-name "$TARGET_NAME" \
            --policy-name "$policy_name" 2>&1 | grep -q "InvalidClientTokenId" && return 1
    fi
    
    return 0
}

# Apply all policies
cmd_apply() {
    log_info "Applying policies to $TARGET_TYPE '$TARGET_NAME'"
    [ $DRY_RUN -eq 1 ] && log_warning "DRY-RUN MODE - no changes will be made"
    echo ""
    
    local success=0
    local failed=0
    
    for policy in "${POLICIES[@]}"; do
        printf "  %-25s " "$policy..."
        
        if apply_policy "$policy"; then
            log_success ""
            ((success++))
        else
            log_error ""
            ((failed++))
        fi
    done
    
    echo ""
    log_info "Summary: $success applied, $failed failed"
    
    if [ $failed -eq 0 ]; then
        log_success "All policies applied successfully!"
        echo ""
        echo "Next steps:"
        echo "  1. Verify: $0 verify -t $TARGET_NAME"
        echo "  2. Deploy: cd infrastructure && cdk bootstrap"
        return 0
    else
        log_error "Some policies failed to apply"
        return 1
    fi
}

# Remove all policies
cmd_remove() {
    log_info "Removing policies from $TARGET_TYPE '$TARGET_NAME'"
    [ $DRY_RUN -eq 1 ] && log_warning "DRY-RUN MODE - no changes will be made"
    echo ""
    
    local success=0
    local failed=0
    
    for policy in "${POLICIES[@]}"; do
        printf "  %-25s " "$policy..."
        
        if remove_policy "$policy"; then
            log_success ""
            ((success++))
        else
            log_error ""
            ((failed++))
        fi
    done
    
    echo ""
    log_info "Summary: $success removed, $failed failed"
    
    if [ $failed -eq 0 ]; then
        log_success "All policies removed successfully!"
        return 0
    else
        log_warning "Some policies may still be attached"
        return 1
    fi
}

# List applied policies
cmd_list() {
    log_info "Applied policies for $TARGET_TYPE '$TARGET_NAME':"
    echo ""
    
    local policies
    if [ "$TARGET_TYPE" = "group" ]; then
        policies=$(aws iam list-group-policies --group-name "$TARGET_NAME" --query 'PolicyNames[]' --output text)
    else
        policies=$(aws iam list-user-policies --user-name "$TARGET_NAME" --query 'PolicyNames[]' --output text)
    fi
    
    if [ -z "$policies" ]; then
        log_warning "No inline policies applied"
        return 0
    fi
    
    for policy in $policies; do
        echo "  • $policy"
    done
    
    return 0
}

# Verify all required policies are applied
cmd_verify() {
    log_info "Verifying required policies for $TARGET_TYPE '$TARGET_NAME'..."
    echo ""
    
    local applied_policies
    if [ "$TARGET_TYPE" = "group" ]; then
        applied_policies=$(aws iam list-group-policies --group-name "$TARGET_NAME" --query 'PolicyNames[]' --output text)
    else
        applied_policies=$(aws iam list-user-policies --user-name "$TARGET_NAME" --query 'PolicyNames[]' --output text)
    fi
    
    local missing=0
    local ok=0
    
    for required in "${REQUIRED_POLICIES[@]}"; do
        if echo "$applied_policies" | grep -q "$required"; then
            echo "  ${GREEN}✓${NC} $required"
            ((ok++))
        else
            echo "  ${RED}✗${NC} $required (MISSING)"
            ((missing++))
        fi
    done
    
    echo ""
    log_info "Required: $ok applied, $missing missing"
    
    if [ $missing -eq 0 ]; then
        log_success "All required policies are applied!"
        return 0
    else
        log_error "Missing $missing required policies"
        log_info "Run: $0 apply -t $TARGET_NAME"
        return 1
    fi
}

# Show detailed status
cmd_status() {
    log_info "Detailed status for $TARGET_TYPE '$TARGET_NAME'"
    echo ""
    
    # Target info
    if [ "$TARGET_TYPE" = "group" ]; then
        log_info "Group Information:"
        aws iam get-group --group-name "$TARGET_NAME" --query 'Group.[GroupName,GroupId,CreateDate,Arn]' --output text | \
            awk '{print "  Group: " $1 "\n  Group ID: " $2 "\n  Created: " $3 "\n  ARN: " $4}'
        
        # Members
        log_info "Group Members:"
        local members=$(aws iam get-group --group-name "$TARGET_NAME" --query 'Users[].UserName' --output text)
        if [ -z "$members" ]; then
            echo "  (none)"
        else
            for user in $members; do
                echo "  • $user"
            done
        fi
    else
        log_info "User Information:"
        aws iam get-user --user-name "$TARGET_NAME" --query 'User.[UserName,UserId,CreateDate,Arn]' --output text | \
            awk '{print "  Username: " $1 "\n  User ID: " $2 "\n  Created: " $3 "\n  ARN: " $4}'
    fi
    echo ""
    
    # Account info
    log_info "AWS Account:"
    ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
    echo "  Account ID: $ACCOUNT"
    echo ""
    
    # Applied policies
    log_info "Applied Inline Policies:"
    local applied
    if [ "$TARGET_TYPE" = "group" ]; then
        applied=$(aws iam list-group-policies --group-name "$TARGET_NAME" --query 'PolicyNames[]' --output text)
    else
        applied=$(aws iam list-user-policies --user-name "$TARGET_NAME" --query 'PolicyNames[]' --output text)
    fi
    
    if [ -z "$applied" ]; then
        echo "  (none)"
    else
        for policy in $applied; do
            if [ "$TARGET_TYPE" = "group" ]; then
                local size=$(aws iam get-group-policy --group-name "$TARGET_NAME" --policy-name "$policy" --query 'GroupPolicy.PolicyDocument' --output json | wc -c)
            else
                local size=$(aws iam get-user-policy --user-name "$TARGET_NAME" --policy-name "$policy" --query 'UserPolicy.PolicyDocument' --output json | wc -c)
            fi
            printf "  • %-30s (%d bytes)\n" "$policy" "$size"
        done
    fi
    echo ""
    
    # Required policy check
    log_info "Required Policies Status:"
    if [ "$TARGET_TYPE" = "group" ]; then
        local applied_policies=$(aws iam list-group-policies --group-name "$TARGET_NAME" --query 'PolicyNames[]' --output text)
    else
        local applied_policies=$(aws iam list-user-policies --user-name "$TARGET_NAME" --query 'PolicyNames[]' --output text)
    fi
    local required_missing=0
    
    for required in "${REQUIRED_POLICIES[@]}"; do
        if echo "$applied_policies" | grep -q "$required"; then
            echo "  ${GREEN}✓${NC} $required"
        else
            echo "  ${RED}✗${NC} $required"
            ((required_missing++))
        fi
    done
    
    echo ""
    if [ $required_missing -eq 0 ]; then
        log_success "$TARGET_TYPE '$TARGET_NAME' is ready to deploy Slay!"
    else
        log_warning "Missing $required_missing required policies"
    fi
    
    return 0
}

# Parse command line arguments
parse_args() {
    local cmd=$1
    shift
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            -t|--target)
                TARGET_NAME="$2"
                shift 2
                ;;
            --type)
                TARGET_TYPE="$2"
                shift 2
                ;;
            -u|--user)
                TARGET_NAME="$2"
                TARGET_TYPE="user"
                shift 2
                ;;
            -a|--account)
                ACCOUNT_ID="$2"
                shift 2
                ;;
            -d|--dry-run)
                DRY_RUN=1
                shift
                ;;
            -v|--verbose)
                VERBOSE=1
                shift
                ;;
            -r|--retries)
                RETRY_COUNT="$2"
                shift 2
                ;;
            --delay)
                RETRY_DELAY="$2"
                shift 2
                ;;
            *)
                log_error "Unknown option: $1"
                usage
                ;;
        esac
    done
}

# Main
main() {
    local cmd=$1
    
    if [ -z "$cmd" ] || [ "$cmd" = "help" ] || [ "$cmd" = "-h" ] || [ "$cmd" = "--help" ]; then
        usage
    fi
    
    check_aws_cli
    
    case $cmd in
        apply)
            parse_args "$@"
            [ -z "$TARGET_NAME" ] && { log_error "$TARGET_TYPE name required"; exit 1; }
            check_credentials
            get_account_id
            check_target_exists
            validate_policies
            cmd_apply
            ;;
        remove)
            parse_args "$@"
            [ -z "$TARGET_NAME" ] && { log_error "$TARGET_TYPE name required"; exit 1; }
            check_credentials
            check_target_exists
            cmd_remove
            ;;
        list)
            parse_args "$@"
            [ -z "$TARGET_NAME" ] && { log_error "$TARGET_TYPE name required"; exit 1; }
            check_credentials
            check_target_exists
            cmd_list
            ;;
        verify)
            parse_args "$@"
            [ -z "$TARGET_NAME" ] && { log_error "$TARGET_TYPE name required"; exit 1; }
            check_credentials
            check_target_exists
            cmd_verify
            ;;
        status)
            parse_args "$@"
            [ -z "$TARGET_NAME" ] && { log_error "$TARGET_TYPE name required"; exit 1; }
            check_credentials
            check_target_exists
            cmd_status
            ;;
        validate)
            validate_policies
            ;;
        *)
            log_error "Unknown command: $cmd"
            usage
            ;;
    esac
}

# Run main
main "$@"
