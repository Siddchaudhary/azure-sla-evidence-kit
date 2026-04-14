"""Resource discovery using Azure Resource Graph."""

import logging
from typing import Any

from azure.identity import DefaultAzureCredential
from azure.mgmt.resourcegraph import ResourceGraphClient
from azure.mgmt.resourcegraph.models import QueryRequest

from azsla.models import ResourceRecord

logger = logging.getLogger(__name__)

# Resource Graph queries for supported resource types
DISCOVERY_QUERIES: dict[str, str] = {
    # Compute
    "virtualMachines": """
        Resources
        | where type =~ 'Microsoft.Compute/virtualMachines'
        | extend powerState = tostring(properties.extended.instanceView.powerState.code)
        | where powerState =~ 'PowerState/running' or isnull(powerState)
        | project id, name, type, subscriptionId, resourceGroup, location, tags,
                  sku = tostring(properties.hardwareProfile.vmSize),
                  tier = tostring(sku.tier),
                  properties
    """,
    # App Services / Web Apps
    "appServices": """
        Resources
        | where type =~ 'Microsoft.Web/sites'
        | where properties.state =~ 'Running'
        | project id, name, type, subscriptionId, resourceGroup, location, tags,
                  sku = tostring(properties.sku),
                  tier = tostring(properties.siteConfig.appServicePlanId),
                  properties
    """,
    # Azure Kubernetes Service
    "aksClusters": """
        Resources
        | where type =~ 'Microsoft.ContainerService/managedClusters'
        | where properties.provisioningState =~ 'Succeeded'
        | project id, name, type, subscriptionId, resourceGroup, location, tags,
                  sku = tostring(sku.name),
                  tier = tostring(sku.tier),
                  properties
    """,
    # Azure SQL Database
    "sqlDatabases": """
        Resources
        | where type =~ 'Microsoft.Sql/servers/databases'
        | where name != 'master'
        | project id, name, type, subscriptionId, resourceGroup, location, tags,
                  sku = tostring(sku.name),
                  tier = tostring(sku.tier),
                  properties
    """,
    # Storage Accounts
    "storageAccounts": """
        Resources
        | where type =~ 'Microsoft.Storage/storageAccounts'
        | where properties.provisioningState =~ 'Succeeded'
        | project id, name, type, subscriptionId, resourceGroup, location, tags,
                  sku = tostring(sku.name),
                  tier = tostring(sku.tier),
                  properties
    """,
    # PostgreSQL Flexible Server
    "postgresqlFlexible": """
        Resources
        | where type =~ 'Microsoft.DBforPostgreSQL/flexibleServers'
        | where properties.state =~ 'Ready'
        | project id, name, type, subscriptionId, resourceGroup, location, tags,
                  sku = tostring(sku.name),
                  tier = tostring(sku.tier),
                  properties
    """,
    # Container Apps
    "containerApps": """
        Resources
        | where type =~ 'Microsoft.App/containerApps'
        | where properties.provisioningState =~ 'Succeeded'
        | project id, name, type, subscriptionId, resourceGroup, location, tags,
                  sku = '',
                  tier = '',
                  properties
    """,
    # Azure Front Door / CDN
    "frontDoorCdn": """
        Resources
        | where type =~ 'Microsoft.Cdn/profiles'
        | where properties.provisioningState =~ 'Succeeded'
        | project id, name, type, subscriptionId, resourceGroup, location, tags,
                  sku = tostring(sku.name),
                  tier = tostring(sku.tier),
                  properties
    """,
    # Azure Bastion
    "bastionHosts": """
        Resources
        | where type =~ 'Microsoft.Network/bastionHosts'
        | where properties.provisioningState =~ 'Succeeded'
        | project id, name, type, subscriptionId, resourceGroup, location, tags,
                  sku = tostring(sku.name),
                  tier = '',
                  properties
    """,
    # Virtual Network Gateway (VPN/ExpressRoute)
    "vnetGateways": """
        Resources
        | where type =~ 'Microsoft.Network/virtualNetworkGateways'
        | where properties.provisioningState =~ 'Succeeded'
        | project id, name, type, subscriptionId, resourceGroup, location, tags,
                  sku = tostring(properties.sku.name),
                  tier = tostring(properties.sku.tier),
                  properties
    """,
    # Azure Firewall
    "azureFirewall": """
        Resources
        | where type =~ 'Microsoft.Network/azureFirewalls'
        | where properties.provisioningState =~ 'Succeeded'
        | project id, name, type, subscriptionId, resourceGroup, location, tags,
                  sku = tostring(sku.name),
                  tier = tostring(sku.tier),
                  properties
    """,
    # Load Balancer (Standard SKU)
    "loadBalancers": """
        Resources
        | where type =~ 'Microsoft.Network/loadBalancers'
        | where sku.name =~ 'Standard'
        | project id, name, type, subscriptionId, resourceGroup, location, tags,
                  sku = tostring(sku.name),
                  tier = tostring(sku.tier),
                  properties
    """,
    # Application Gateway
    "appGateways": """
        Resources
        | where type =~ 'Microsoft.Network/applicationGateways'
        | where properties.provisioningState =~ 'Succeeded'
        | project id, name, type, subscriptionId, resourceGroup, location, tags,
                  sku = tostring(sku.name),
                  tier = tostring(sku.tier),
                  properties
    """,
    # Key Vault
    "keyVaults": """
        Resources
        | where type =~ 'Microsoft.KeyVault/vaults'
        | project id, name, type, subscriptionId, resourceGroup, location, tags,
                  sku = tostring(sku.name),
                  tier = '',
                  properties
    """,
    # Cosmos DB
    "cosmosDb": """
        Resources
        | where type =~ 'Microsoft.DocumentDB/databaseAccounts'
        | where properties.provisioningState =~ 'Succeeded'
        | project id, name, type, subscriptionId, resourceGroup, location, tags,
                  sku = '',
                  tier = tostring(properties.databaseAccountOfferType),
                  properties
    """,
    # Azure Cache for Redis
    "redisCache": """
        Resources
        | where type =~ 'Microsoft.Cache/Redis'
        | where properties.provisioningState =~ 'Succeeded'
        | project id, name, type, subscriptionId, resourceGroup, location, tags,
                  sku = tostring(sku.name),
                  tier = tostring(sku.tier),
                  properties
    """,
    # Service Bus
    "serviceBus": """
        Resources
        | where type =~ 'Microsoft.ServiceBus/namespaces'
        | where properties.provisioningState =~ 'Succeeded'
        | project id, name, type, subscriptionId, resourceGroup, location, tags,
                  sku = tostring(sku.name),
                  tier = tostring(sku.tier),
                  properties
    """,
    # Event Hubs
    "eventHubs": """
        Resources
        | where type =~ 'Microsoft.EventHub/namespaces'
        | where properties.provisioningState =~ 'Succeeded'
        | project id, name, type, subscriptionId, resourceGroup, location, tags,
                  sku = tostring(sku.name),
                  tier = tostring(sku.tier),
                  properties
    """,
    # Cognitive Services / Azure AI
    "cognitiveServices": """
        Resources
        | where type =~ 'Microsoft.CognitiveServices/accounts'
        | where properties.provisioningState =~ 'Succeeded'
        | project id, name, type, subscriptionId, resourceGroup, location, tags,
                  sku = tostring(sku.name),
                  tier = tostring(sku.tier),
                  properties
    """,
    # Azure Functions (separate from sites)
    "functionApps": """
        Resources
        | where type =~ 'Microsoft.Web/sites'
        | where kind contains 'functionapp'
        | where properties.state =~ 'Running'
        | project id, name, type, subscriptionId, resourceGroup, location, tags,
                  sku = tostring(properties.sku),
                  tier = 'Function',
                  properties
    """,
    # ExpressRoute Circuits
    "expressRoute": """
        Resources
        | where type =~ 'Microsoft.Network/expressRouteCircuits'
        | where properties.provisioningState =~ 'Succeeded'
        | project id, name, type, subscriptionId, resourceGroup, location, tags,
                  sku = tostring(sku.name),
                  tier = tostring(sku.tier),
                  properties
    """,
    # Public IP Addresses (Standard SKU)
    "publicIPs": """
        Resources
        | where type =~ 'Microsoft.Network/publicIPAddresses'
        | where sku.name =~ 'Standard'
        | project id, name, type, subscriptionId, resourceGroup, location, tags,
                  sku = tostring(sku.name),
                  tier = tostring(sku.tier),
                  properties
    """,
}


def _parse_resource(row: dict[str, Any]) -> ResourceRecord:
    """Parse a Resource Graph row into a ResourceRecord."""
    return ResourceRecord(
        id=row.get("id", ""),
        name=row.get("name", ""),
        type=row.get("type", ""),
        subscription_id=row.get("subscriptionId", ""),
        resource_group=row.get("resourceGroup", ""),
        location=row.get("location", ""),
        tags=row.get("tags") or {},
        sku=row.get("sku"),
        tier=row.get("tier"),
        properties=row.get("properties") or {},
    )


def _run_query(
    client: ResourceGraphClient,
    query: str,
    subscription_ids: list[str],
) -> list[dict[str, Any]]:
    """Execute a Resource Graph query with pagination."""
    results: list[dict[str, Any]] = []
    skip_token: str | None = None

    while True:
        request = QueryRequest(
            subscriptions=subscription_ids,
            query=query,
            options={"$skipToken": skip_token} if skip_token else None,
        )

        response = client.resources(request)
        data = response.data

        if isinstance(data, list):
            results.extend(data)
        elif hasattr(data, "rows") and hasattr(data, "columns"):
            # Table format response
            columns = [col.name for col in data.columns]
            for row in data.rows:
                results.append(dict(zip(columns, row)))

        skip_token = response.skip_token
        if not skip_token:
            break

    return results


def discover_resources(
    subscription_ids: list[str],
    resource_types: list[str] | None = None,
    credential: DefaultAzureCredential | None = None,
) -> list[ResourceRecord]:
    """
    Discover running Azure resources across subscriptions.

    Args:
        subscription_ids: List of subscription IDs to scan
        resource_types: Optional filter for specific resource types (keys from DISCOVERY_QUERIES)
        credential: Azure credential (uses DefaultAzureCredential if not provided)

    Returns:
        List of discovered ResourceRecord objects
    """
    if credential is None:
        credential = DefaultAzureCredential()

    client = ResourceGraphClient(credential)

    # Determine which queries to run
    queries_to_run = resource_types or list(DISCOVERY_QUERIES.keys())

    all_resources: list[ResourceRecord] = []

    for query_name in queries_to_run:
        if query_name not in DISCOVERY_QUERIES:
            logger.warning(f"Unknown resource type query: {query_name}")
            continue

        query = DISCOVERY_QUERIES[query_name]
        logger.info(f"Discovering {query_name} across {len(subscription_ids)} subscription(s)")

        try:
            rows = _run_query(client, query, subscription_ids)
            resources = [_parse_resource(row) for row in rows]
            logger.info(f"Found {len(resources)} {query_name}")
            all_resources.extend(resources)
        except Exception as e:
            logger.error(f"Failed to discover {query_name}: {e}")

    logger.info(f"Total resources discovered: {len(all_resources)}")
    return all_resources


def discover_custom_query(
    subscription_ids: list[str],
    query: str,
    credential: DefaultAzureCredential | None = None,
) -> list[ResourceRecord]:
    """
    Run a custom Resource Graph query and return ResourceRecords.

    Args:
        subscription_ids: List of subscription IDs to scan
        query: Custom KQL query (must project standard fields)
        credential: Azure credential

    Returns:
        List of ResourceRecord objects
    """
    if credential is None:
        credential = DefaultAzureCredential()

    client = ResourceGraphClient(credential)
    rows = _run_query(client, query, subscription_ids)
    return [_parse_resource(row) for row in rows]
