#!/usr/bin/env bash
# Deploy the unified www-contact Lambda + ensure /contact and /apply
# routes exist on the existing HTTP API.
#
# Idempotent. Re-runnable. Updates code + config; preserves existing
# CORS fields and only mutates AllowOrigins.
#
# Requires: aws cli v2, jq, valid AWS creds.
#
# Usage:
#   ./deploy.sh
#
# Env overrides:
#   AWS_PROFILE   default: 138893339755-goflight-production
#   AWS_REGION    default: us-east-1
#   FUNCTION      default: www-contact
#   API_ID        default: 8pm6qmjog0
#   CONTACT_TO    default: hello@goflight.ai
#   APPLY_TO      default: jack@goflight.ai
#   FROM_EMAIL    default: hello@goflight.ai

set -euo pipefail

AWS_PROFILE="${AWS_PROFILE:-138893339755-goflight-production}"
AWS_REGION="${AWS_REGION:-us-east-1}"
FUNCTION="${FUNCTION:-www-contact}"
API_ID="${API_ID:-8pm6qmjog0}"
CONTACT_TO="${CONTACT_TO:-hello@goflight.ai}"
APPLY_TO="${APPLY_TO:-jack@goflight.ai}"
FROM_EMAIL="${FROM_EMAIL:-hello@goflight.ai}"

aws() { command aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"; }

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "→ Account: $ACCOUNT_ID  Region: $AWS_REGION  Function: $FUNCTION"

# ─── 1. Confirm Lambda exists (this script updates, doesn't create) ──────────
if ! aws lambda get-function --function-name "$FUNCTION" >/dev/null 2>&1; then
  echo "✗ Lambda '$FUNCTION' does not exist." >&2
  echo "  This script updates an existing function. Create it once via console" >&2
  echo "  or another bootstrap script, then re-run." >&2
  exit 1
fi

# ─── 2. Ensure IAM role grants ses:SendRawEmail (needed for attachments) ─────
ROLE_ARN=$(aws lambda get-function --function-name "$FUNCTION" --query 'Configuration.Role' --output text)
ROLE_NAME="${ROLE_ARN##*/}"
HAS_RAW="no"
for p in $(aws iam list-role-policies --role-name "$ROLE_NAME" --query 'PolicyNames[]' --output text); do
  if aws iam get-role-policy --role-name "$ROLE_NAME" --policy-name "$p" \
      --query 'PolicyDocument.Statement[].Action' --output text 2>/dev/null \
      | tr '\t' '\n' | grep -q 'ses:SendRawEmail'; then
    HAS_RAW="yes"
    break
  fi
done
if [[ "$HAS_RAW" != "yes" ]]; then
  echo "→ Role $ROLE_NAME missing ses:SendRawEmail — patching ses-send policy"
  aws iam put-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-name ses-send \
    --policy-document '{
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Action": ["ses:SendEmail", "ses:SendRawEmail"],
        "Resource": "*"
      }]
    }'
  echo "→ Waiting 5s for IAM propagation"
  sleep 5
fi

# ─── 3. Package ──────────────────────────────────────────────────────────────
HERE="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$HERE/.build"
ZIP_PATH="$BUILD_DIR/$FUNCTION.zip"
rm -rf "$BUILD_DIR" && mkdir -p "$BUILD_DIR"
cp "$HERE/index.py" "$BUILD_DIR/"
( cd "$BUILD_DIR" && zip -q "$ZIP_PATH" index.py )
echo "→ Packaged $(du -h "$ZIP_PATH" | cut -f1) at $ZIP_PATH"

# ─── 4. Update Lambda code + config ──────────────────────────────────────────
echo "→ Updating Lambda code"
aws lambda update-function-code \
  --function-name "$FUNCTION" \
  --zip-file "fileb://$ZIP_PATH" >/dev/null
aws lambda wait function-updated --function-name "$FUNCTION"

echo "→ Updating Lambda configuration"
ENV_VARS="Variables={FROM_EMAIL=$FROM_EMAIL,CONTACT_TO=$CONTACT_TO,APPLY_TO=$APPLY_TO,FROM_NAME=GoFlight}"
aws lambda update-function-configuration \
  --function-name "$FUNCTION" \
  --handler index.handler \
  --runtime python3.12 \
  --timeout 15 \
  --memory-size 256 \
  --environment "$ENV_VARS" >/dev/null
aws lambda wait function-updated --function-name "$FUNCTION"

LAMBDA_ARN=$(aws lambda get-function --function-name "$FUNCTION" --query 'Configuration.FunctionArn' --output text)
echo "→ Lambda: $LAMBDA_ARN"

# ─── 5. API Gateway — ensure integration + /contact + /apply routes ──────────
INTEGRATION_ID=$(aws apigatewayv2 get-integrations --api-id "$API_ID" \
  --query "Items[?contains(IntegrationUri, ':function:$FUNCTION')].IntegrationId | [0]" --output text 2>/dev/null || echo "None")

if [[ -z "$INTEGRATION_ID" || "$INTEGRATION_ID" == "None" ]]; then
  echo "→ Creating API GW integration"
  INTEGRATION_ID=$(aws apigatewayv2 create-integration \
    --api-id "$API_ID" \
    --integration-type AWS_PROXY \
    --integration-uri "$LAMBDA_ARN" \
    --payload-format-version 2.0 \
    --query 'IntegrationId' --output text)
fi
echo "→ Integration: $INTEGRATION_ID"

ensure_route() {
  local route_key="$1"
  local route_id
  route_id=$(aws apigatewayv2 get-routes --api-id "$API_ID" \
    --query "Items[?RouteKey=='$route_key'].RouteId | [0]" --output text)
  if [[ -z "$route_id" || "$route_id" == "None" ]]; then
    echo "→ Creating route $route_key"
    aws apigatewayv2 create-route \
      --api-id "$API_ID" \
      --route-key "$route_key" \
      --target "integrations/$INTEGRATION_ID" >/dev/null
  else
    aws apigatewayv2 update-route \
      --api-id "$API_ID" \
      --route-id "$route_id" \
      --target "integrations/$INTEGRATION_ID" >/dev/null
    echo "→ Route $route_key exists (target refreshed)"
  fi
}
ensure_route "POST /contact"
ensure_route "POST /apply"

# ─── 6. CORS — add join.goflight.ai, preserve all other fields ───────────────
CURRENT_CORS=$(aws apigatewayv2 get-api --api-id "$API_ID" --query 'CorsConfiguration' --output json 2>/dev/null || echo 'null')
echo "→ Current CORS: $CURRENT_CORS"
if echo "$CURRENT_CORS" | jq -e '(.AllowOrigins // []) | index("https://join.goflight.ai") | not' >/dev/null; then
  echo "→ Adding https://join.goflight.ai to AllowOrigins (preserving other fields)"
  PATCH_FILE="$BUILD_DIR/cors-patch.json"
  echo "$CURRENT_CORS" | jq --arg api "$API_ID" '
    (. // {AllowOrigins: [], AllowMethods: ["POST","OPTIONS"], AllowHeaders: ["content-type"], MaxAge: 86400})
    | .AllowOrigins = ((.AllowOrigins // []) + ["https://join.goflight.ai"] | unique)
    | {ApiId: $api, CorsConfiguration: .}
  ' > "$PATCH_FILE"
  cat "$PATCH_FILE"
  aws apigatewayv2 update-api --cli-input-json "file://$PATCH_FILE" >/dev/null
else
  echo "→ CORS already includes https://join.goflight.ai"
fi

# ─── 7. Lambda invoke permission for both routes ─────────────────────────────
grant_permission() {
  local route_path="$1"   # e.g. "POST/contact"
  local stmt_id="apigw-$API_ID-$(echo "$route_path" | tr '/' '-' | tr '[:upper:]' '[:lower:]')"
  if ! aws lambda get-policy --function-name "$FUNCTION" 2>/dev/null | grep -q "\"$stmt_id\""; then
    echo "→ Granting invoke permission for $route_path ($stmt_id)"
    aws lambda add-permission \
      --function-name "$FUNCTION" \
      --statement-id "$stmt_id" \
      --action lambda:InvokeFunction \
      --principal apigateway.amazonaws.com \
      --source-arn "arn:aws:execute-api:$AWS_REGION:$ACCOUNT_ID:$API_ID/*/$route_path" >/dev/null
  fi
}
grant_permission "POST/contact"
grant_permission "POST/apply"

cat <<EOF

✓ Deployed.
  POST  https://${API_ID}.execute-api.${AWS_REGION}.amazonaws.com/contact   (landing form)
  POST  https://${API_ID}.execute-api.${AWS_REGION}.amazonaws.com/apply     (hiring form)

  Smoke test — landing form:
  curl -sS -X POST https://${API_ID}.execute-api.${AWS_REGION}.amazonaws.com/contact \\
    -H 'Content-Type: application/json' -H 'Origin: https://goflight.ai' \\
    -d '{"name":"Smoke","email":"$CONTACT_TO","role":"other","context":"deploy smoke test"}'

  Smoke test — hiring form (no resume):
  curl -sS -X POST https://${API_ID}.execute-api.${AWS_REGION}.amazonaws.com/apply \\
    -H 'Content-Type: application/json' -H 'Origin: https://join.goflight.ai' \\
    -d '{"name":"Smoke","email":"$APPLY_TO","portfolio":"https://example.com","interesting":"Deploy smoke test from the script — please disregard."}'
EOF
