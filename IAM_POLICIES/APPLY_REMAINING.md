for policy in SlayS3Storage SlayLambdaCompute SlayIAMRoles SlayCloudFormation SlayECRAssets SlayDynamoDB; do
  echo "Applying $policy..."
  aws iam put-user-policy \
    --user-name slay \
    --policy-name "$policy" \
    --policy-document "file://${policy}.json"
done
