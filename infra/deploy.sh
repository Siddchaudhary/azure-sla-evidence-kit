#!/bin/bash
# Deploy Azure SLA Dashboard to Azure Container Apps
# Usage: ./deploy.sh <resource-group> <subscription-ids>

set -e

RESOURCE_GROUP=${1:-"rg-sla-dashboard"}
SUBSCRIPTION_IDS=${2:-""}
LOCATION=${LOCATION:-"eastus"}
IMAGE_TAG=${IMAGE_TAG:-"latest"}
ACR_NAME=${ACR_NAME:-""}

echo "🚀 Deploying Azure SLA Dashboard"
echo "   Resource Group: $RESOURCE_GROUP"
echo "   Location: $LOCATION"

# Create resource group if it doesn't exist
if ! az group show -n "$RESOURCE_GROUP" &>/dev/null; then
    echo "📦 Creating resource group..."
    az group create -n "$RESOURCE_GROUP" -l "$LOCATION"
fi

# Build and push container image if ACR is provided
if [ -n "$ACR_NAME" ]; then
    echo "🐳 Building and pushing container image..."
    az acr build -r "$ACR_NAME" -t "azure-sla-dashboard:$IMAGE_TAG" .
    CONTAINER_IMAGE="$ACR_NAME.azurecr.io/azure-sla-dashboard:$IMAGE_TAG"
else
    CONTAINER_IMAGE="ghcr.io/your-org/azure-sla-dashboard:$IMAGE_TAG"
fi

# Create Log Analytics workspace
echo "📊 Creating Log Analytics workspace..."
WORKSPACE_ID=$(az monitor log-analytics workspace create \
    -g "$RESOURCE_GROUP" \
    -n "sla-dashboard-logs" \
    --query customerId -o tsv 2>/dev/null || \
    az monitor log-analytics workspace show \
    -g "$RESOURCE_GROUP" \
    -n "sla-dashboard-logs" \
    --query customerId -o tsv)

# Deploy infrastructure
echo "🏗️  Deploying infrastructure..."
DEPLOYMENT_OUTPUT=$(az deployment group create \
    -g "$RESOURCE_GROUP" \
    -f infra/main.bicep \
    --parameters \
        containerImage="$CONTAINER_IMAGE" \
        azureSubscriptionIds="$SUBSCRIPTION_IDS" \
        logAnalyticsWorkspaceId="$WORKSPACE_ID" \
    --query 'properties.outputs' -o json)

APP_URL=$(echo "$DEPLOYMENT_OUTPUT" | jq -r '.containerAppUrl.value')
IDENTITY_PRINCIPAL=$(echo "$DEPLOYMENT_OUTPUT" | jq -r '.managedIdentityPrincipalId.value')

echo ""
echo "✅ Deployment complete!"
echo "   Dashboard URL: $APP_URL"
echo ""
echo "📋 Next steps:"
echo "   1. Assign Reader role to the managed identity on your subscriptions:"
echo ""
for SUB_ID in $(echo "$SUBSCRIPTION_IDS" | tr ',' ' '); do
    echo "      az role assignment create \\"
    echo "          --assignee $IDENTITY_PRINCIPAL \\"
    echo "          --role 'Reader' \\"
    echo "          --scope /subscriptions/$SUB_ID"
    echo ""
done
echo "   2. Open the dashboard: $APP_URL"
echo "   3. Configure subscriptions in Settings"
