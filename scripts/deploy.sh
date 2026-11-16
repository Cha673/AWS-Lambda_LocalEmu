#!/usr/bin/env bash
# Crée ou met à jour toutes les ressources du projet dans LocalEmu.
# Peut être relancé sans risque (idempotent).
set -euo pipefail

cd "$(dirname "$0")/.."
source scripts/config.sh

etape() { echo; echo "=== $1 ==="; }

# ---------------------------------------------------------------------------
etape "0. Vérification de la cible"
verifier_emulateur

# ---------------------------------------------------------------------------
etape "1. Files SQS"
mkdir -p "$BUILD_DIR"

DLQ_URL=$(aws_local sqs create-queue --queue-name "$DLQ_NAME" --query QueueUrl --output text)
DLQ_ARN=$(aws_local sqs get-queue-attributes --queue-url "$DLQ_URL" \
    --attribute-names QueueArn --query Attributes.QueueArn --output text)

QUEUE_URL=$(aws_local sqs create-queue --queue-name "$QUEUE_NAME" --query QueueUrl --output text)
QUEUE_ARN=$(aws_local sqs get-queue-attributes --queue-url "$QUEUE_URL" \
    --attribute-names QueueArn --query Attributes.QueueArn --output text)

# Après 3 échecs, un message part dans la file d'erreurs (DLQ)
cat > "$BUILD_DIR/queue-attributes.json" <<EOF
{
  "VisibilityTimeout": "30",
  "RedrivePolicy": "{\"deadLetterTargetArn\":\"$DLQ_ARN\",\"maxReceiveCount\":\"3\"}"
}
EOF
aws_local sqs set-queue-attributes --queue-url "$QUEUE_URL" \
    --attributes "file://$BUILD_DIR/queue-attributes.json"

echo "  file principale : $QUEUE_URL"
echo "  file d'erreurs  : $DLQ_URL"

# ---------------------------------------------------------------------------
etape "2. Table DynamoDB"
if aws_local dynamodb describe-table --table-name "$TABLE_NAME" >/dev/null 2>&1; then
    echo "  table $TABLE_NAME déjà présente"
else
    aws_local dynamodb create-table \
        --table-name "$TABLE_NAME" \
        --attribute-definitions AttributeName=id,AttributeType=S \
        --key-schema AttributeName=id,KeyType=HASH \
        --billing-mode PAY_PER_REQUEST >/dev/null
    aws_local dynamodb wait table-exists --table-name "$TABLE_NAME"
    echo "  table $TABLE_NAME créée"
fi

# ---------------------------------------------------------------------------
etape "3. Rôle IAM des Lambda"
if ROLE_ARN=$(aws_local iam get-role --role-name "$ROLE_NAME" \
        --query Role.Arn --output text 2>/dev/null); then
    echo "  rôle déjà présent"
else
    ROLE_ARN=$(aws_local iam create-role \
        --role-name "$ROLE_NAME" \
        --assume-role-policy-document file://infra/trust-policy.json \
        --query Role.Arn --output text)
    echo "  rôle créé"
fi
aws_local iam put-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-name commandes-permissions \
    --policy-document file://infra/lambda-policy.json
echo "  $ROLE_ARN"

# ---------------------------------------------------------------------------
etape "4. Empaquetage du code"
rm -f "$BUILD_DIR"/*.zip
# handler.py est placé à la racine du zip, d'où le handler "handler.handler"
python3 -m zipfile -c "$BUILD_DIR/lambda_a.zip" src/lambda_a_http/handler.py
python3 -m zipfile -c "$BUILD_DIR/lambda_b.zip" src/lambda_b_sqs/handler.py
echo "  $BUILD_DIR/lambda_a.zip et $BUILD_DIR/lambda_b.zip prêts"

# ---------------------------------------------------------------------------
etape "5. Fonctions Lambda"

deployer_lambda() {
    local nom=$1 zip=$2 variables=$3
    if aws_local lambda get-function --function-name "$nom" >/dev/null 2>&1; then
        aws_local lambda update-function-code \
            --function-name "$nom" --zip-file "fileb://$zip" >/dev/null
        aws_local lambda wait function-updated --function-name "$nom"
        aws_local lambda update-function-configuration \
            --function-name "$nom" --environment "$variables" --timeout 10 >/dev/null
        echo "  $nom mise à jour"
    else
        aws_local lambda create-function \
            --function-name "$nom" \
            --runtime "$RUNTIME" \
            --handler handler.handler \
            --role "$ROLE_ARN" \
            --zip-file "fileb://$zip" \
            --timeout 10 \
            --memory-size 128 \
            --environment "$variables" >/dev/null
        echo "  $nom créée"
    fi
    aws_local lambda wait function-active --function-name "$nom"
    aws_local lambda wait function-updated --function-name "$nom"
}

deployer_lambda "$LAMBDA_A" "$BUILD_DIR/lambda_a.zip" \
    "{\"Variables\":{\"QUEUE_URL\":\"$QUEUE_URL\"}}"
deployer_lambda "$LAMBDA_B" "$BUILD_DIR/lambda_b.zip" \
    "{\"Variables\":{\"TABLE_NAME\":\"$TABLE_NAME\"}}"

# ---------------------------------------------------------------------------
etape "6. Déclencheur SQS → Lambda B"
ESM_UUID=$(aws_local lambda list-event-source-mappings \
    --function-name "$LAMBDA_B" --event-source-arn "$QUEUE_ARN" \
    --query 'EventSourceMappings[0].UUID' --output text)

if [[ -z "$ESM_UUID" || "$ESM_UUID" == "None" ]]; then
    ESM_UUID=$(aws_local lambda create-event-source-mapping \
        --function-name "$LAMBDA_B" \
        --event-source-arn "$QUEUE_ARN" \
        --batch-size 5 \
        --function-response-types ReportBatchItemFailures \
        --query UUID --output text)
    echo "  déclencheur créé ($ESM_UUID)"
else
    # On le réactive au cas où un test l'aurait laissé désactivé
    aws_local lambda update-event-source-mapping --uuid "$ESM_UUID" --enabled >/dev/null
    echo "  déclencheur déjà présent, activé ($ESM_UUID)"
fi

# ---------------------------------------------------------------------------
etape "7. API Gateway → Lambda A"
# On repart d'une API propre à chaque déploiement
for ancien in $(aws_local apigateway get-rest-apis \
        --query "items[?name=='$API_NAME'].id" --output text); do
    aws_local apigateway delete-rest-api --rest-api-id "$ancien"
done

API_ID=$(aws_local apigateway create-rest-api --name "$API_NAME" --query id --output text)
RACINE_ID=$(aws_local apigateway get-resources --rest-api-id "$API_ID" \
    --query "items[?path=='/'].id" --output text)
RESSOURCE_ID=$(aws_local apigateway create-resource --rest-api-id "$API_ID" \
    --parent-id "$RACINE_ID" --path-part commandes --query id --output text)

aws_local apigateway put-method \
    --rest-api-id "$API_ID" --resource-id "$RESSOURCE_ID" \
    --http-method POST --authorization-type NONE >/dev/null

LAMBDA_A_ARN=$(aws_local lambda get-function --function-name "$LAMBDA_A" \
    --query Configuration.FunctionArn --output text)

# AWS_PROXY : API Gateway transmet la requête telle quelle à la Lambda
aws_local apigateway put-integration \
    --rest-api-id "$API_ID" --resource-id "$RESSOURCE_ID" \
    --http-method POST \
    --type AWS_PROXY \
    --integration-http-method POST \
    --uri "arn:aws:apigateway:${AWS_DEFAULT_REGION}:lambda:path/2015-03-31/functions/${LAMBDA_A_ARN}/invocations" >/dev/null

# Autoriser API Gateway à invoquer la Lambda A
aws_local lambda remove-permission --function-name "$LAMBDA_A" \
    --statement-id apigateway-invoke >/dev/null 2>&1 || true
aws_local lambda add-permission \
    --function-name "$LAMBDA_A" \
    --statement-id apigateway-invoke \
    --action lambda:InvokeFunction \
    --principal apigateway.amazonaws.com \
    --source-arn "arn:aws:execute-api:${AWS_DEFAULT_REGION}:${COMPTE_LOCAL}:${API_ID}/*/POST/commandes" >/dev/null

aws_local apigateway create-deployment --rest-api-id "$API_ID" --stage-name "$STAGE" >/dev/null

API_URL="${ENDPOINT}/restapis/${API_ID}/${STAGE}/_user_request_/commandes"
echo "  API $API_ID déployée sur le stage $STAGE"

# ---------------------------------------------------------------------------
etape "8. Récapitulatif"
cat > "$ENV_FILE" <<EOF
API_URL=$API_URL
API_ID=$API_ID
QUEUE_URL=$QUEUE_URL
DLQ_URL=$DLQ_URL
ESM_UUID=$ESM_UUID
EOF

echo "Files SQS :";        aws_local sqs list-queues --query 'QueueUrls' --output text | tr '\t' '\n' | sed 's/^/  /'
echo "Tables DynamoDB :";  aws_local dynamodb list-tables --query 'TableNames' --output text | sed 's/^/  /'
echo "Lambda :";           aws_local lambda list-functions --query 'Functions[].FunctionName' --output text | tr '\t' '\n' | sed 's/^/  /'
echo "Déclencheur :";      aws_local lambda get-event-source-mapping --uuid "$ESM_UUID" --query State --output text | sed 's/^/  /'
echo
echo "✔ Déploiement terminé. URL de l'API :"
echo "  $API_URL"
echo "  (enregistrée dans $ENV_FILE)"