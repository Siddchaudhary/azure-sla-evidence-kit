# Azure SLA Evidence Kit

A Python web dashboard and CLI that discovers Azure resources, collects availability metrics, and generates SLA compliance reports. Features an Azure Portal-inspired UI with real-time monitoring.

> ⚠️ **Disclaimer**: This tool provides informational availability estimates based on Azure Monitor metrics. It is **not** an official SLA attestation tool and should not be used for contractual SLA claims. Always refer to the Azure Portal SLA credit process for official SLA breach claims.

## Features

- 🌐 **Azure Portal-Style Dashboard**: Modern UI matching Azure Portal design language
- 🔍 **Auto-Discovery**: Enumerate resources across multiple subscriptions using Azure Resource Graph
- 📊 **Metrics Collection**: Pull availability signals from Azure Monitor
- 📈 **SLA Trend Charts**: Track compliance trends over 30 days with historical snapshots
- 📋 **SLA Catalog**: Map resource types to published Azure SLAs (configurable)
- 🧮 **Compliance Calculation**: Compare actual uptime vs SLA targets
- 📥 **CSV Export**: Download SLA compliance data for reporting
- ⏰ **Background Collection**: Automatic periodic data collection (configurable interval)
- 💾 **Persistent Storage**: SQLite with Azure Blob Storage backup for container deployments
- 🏥 **Health Endpoints**: `/health` and `/ready` for container orchestration
- ☁️ **Azure Container Apps Ready**: Deploy with managed identity authentication

## Supported Resource Types

| Resource Type | Metrics Strategy | Status |
|--------------|------------------|--------|
| Virtual Machines | VmAvailabilityMetric | ✅ Implemented |
| App Services | Requests/Http5xx | 🚧 Stub |
| AKS Clusters | - | 🚧 Stub |
| SQL Databases | - | 🚧 Stub |

## Prerequisites

- Python 3.11+
- Azure subscription(s) with **Reader** role access
- Azure CLI or environment credentials configured

## Installation

```bash
# Using uv (recommended)
uv sync

# Or using pip
pip install -e .
```

## Configuration

1. Copy the environment template:
   ```bash
   cp .env.example .env
   ```

2. Configure your Azure credentials (choose one method):

   **Option A: Azure CLI (local development)**
   ```bash
   az login
   ```

   **Option B: Service Principal**
   ```bash
   export AZURE_CLIENT_ID="your-client-id"
   export AZURE_CLIENT_SECRET="your-client-secret"
   export AZURE_TENANT_ID="your-tenant-id"
   ```

   **Option C: Managed Identity (Azure VMs/CI)**
   - No configuration needed; `DefaultAzureCredential` auto-detects

## Usage

### Web Dashboard (Recommended)

Start the web dashboard for real-time SLA monitoring:

```bash
# Start the dashboard
azsla-web

# Or with uvicorn directly
uvicorn azsla.web.main:create_app --factory --reload
```

Then open http://localhost:8000 in your browser.

**Dashboard Features:**
- Real-time compliance overview with charts
- Filter by subscription, date range, resource type
- View SLA breaches at a glance
- Drill down into individual resource metrics
- Trigger manual data collection
- Manage monitored subscriptions

### CLI Usage

#### End-to-End Report Generation

```bash
# Generate report for last full calendar month
azsla run --subscriptions sub1,sub2 --out ./outputs

# Explicit date range
azsla run --subscriptions sub1,sub2 --start 2026-03-01 --end 2026-04-01 --out ./outputs
```

### Individual Commands

```bash
# Discover resources
azsla discover --subscriptions sub1,sub2 --out resources.json

# Collect metrics for discovered resources
azsla collect --resources resources.json --start 2026-03-01 --end 2026-04-01 --out metrics.json

# Generate reports from collected data
azsla report --metrics metrics.json --out ./outputs
```

### CLI Help

```bash
azsla --help
azsla run --help
```

## Azure Permissions

This tool requires **read-only** access. Assign these roles at the subscription level:

```bash
# Required: Read resource metadata via Resource Graph
az role assignment create \
  --assignee <principal-id> \
  --role "Reader" \
  --scope /subscriptions/<subscription-id>

# Required: Read availability metrics from Azure Monitor  
az role assignment create \
  --assignee <principal-id> \
  --role "Monitoring Reader" \
  --scope /subscriptions/<subscription-id>

# Optional: For blob storage backup (if using persistent storage)
az role assignment create \
  --assignee <principal-id> \
  --role "Storage Blob Data Contributor" \
  --scope /subscriptions/<subscription-id>/resourceGroups/<rg>/providers/Microsoft.Storage/storageAccounts/<storage-account>
```

## SLA Catalog

Edit `slas/azure_sla_catalog.yaml` to customize SLA targets:

```yaml
resource_types:
  Microsoft.Compute/virtualMachines:
    default_sla: 99.9
    conditions:
      - sku_contains: "Standard"
        availability_set: true
        sla: 99.95
```

## Output Structure

```
outputs/
├── executive_summary.md    # High-level compliance summary
├── detailed_report.html    # Full resource-by-resource breakdown
└── exports/
    ├── resources.csv       # All discovered resources
    └── outages.csv         # Detected availability gaps
```

## GitHub Actions

The included workflows automate testing and deployment:

- `.github/workflows/build-deploy.yml` - Build container and deploy to Azure Container Apps
- `.github/workflows/monthly.yml` - Generate monthly CLI reports (legacy)

### Deploy to Azure Container Apps

1. **Create Azure resources:**
   ```bash
   # Variables
   RG="rg-sla-dashboard"
   LOCATION="australiaeast"
   ACR_NAME="youracr"
   
   # Create resource group
   az group create -n $RG -l $LOCATION
   
   # Create container registry
   az acr create -n $ACR_NAME -g $RG --sku Basic --admin-enabled true
   
   # Create managed identity
   az identity create -n azsla-identity -g $RG -l $LOCATION
   
   # Get identity details
   IDENTITY_ID=$(az identity show -n azsla-identity -g $RG --query id -o tsv)
   PRINCIPAL_ID=$(az identity show -n azsla-identity -g $RG --query principalId -o tsv)
   CLIENT_ID=$(az identity show -n azsla-identity -g $RG --query clientId -o tsv)
   ```

2. **Assign permissions to the managed identity:**
   ```bash
   SUB_ID="your-subscription-id"
   
   az role assignment create --assignee $PRINCIPAL_ID --role "Reader" \
     --scope /subscriptions/$SUB_ID
   
   az role assignment create --assignee $PRINCIPAL_ID --role "Monitoring Reader" \
     --scope /subscriptions/$SUB_ID
   ```

3. **Build and push the container image:**
   ```bash
   az acr build --registry $ACR_NAME \
     --image azure-sla-dashboard:latest .
   ```

4. **Create Container Apps environment and deploy:**
   ```bash
   # Create Log Analytics workspace
   az monitor log-analytics workspace create -g $RG -n sla-dashboard-logs -l $LOCATION
   
   # Create Container Apps environment
   az containerapp env create -n azsla-env -g $RG -l $LOCATION \
     --logs-workspace-id $(az monitor log-analytics workspace show -g $RG -n sla-dashboard-logs --query customerId -o tsv) \
     --logs-workspace-key $(az monitor log-analytics workspace get-shared-keys -g $RG -n sla-dashboard-logs --query primarySharedKey -o tsv)
   
   # Get ACR password
   ACR_PASS=$(az acr credential show -n $ACR_NAME --query passwords[0].value -o tsv)
   
   # Deploy container app
   az containerapp create \
     -n azsla-app -g $RG \
     --environment azsla-env \
     --image $ACR_NAME.azurecr.io/azure-sla-dashboard:latest \
     --registry-server $ACR_NAME.azurecr.io \
     --registry-username $ACR_NAME \
     --registry-password $ACR_PASS \
     --target-port 8000 \
     --ingress external \
     --user-assigned $IDENTITY_ID \
     --cpu 0.5 --memory 1Gi \
     --min-replicas 1 --max-replicas 3 \
     --env-vars \
       AZURE_CLIENT_ID=$CLIENT_ID \
       AZURE_SUBSCRIPTION_IDS=$SUB_ID \
       COLLECTION_ENABLED=true \
       COLLECTION_INTERVAL_HOURS=6
   ```

5. **Get the app URL:**
   ```bash
   az containerapp show -n azsla-app -g $RG --query properties.configuration.ingress.fqdn -o tsv
   ```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `AZURE_CLIENT_ID` | Managed identity client ID | - |
| `AZURE_SUBSCRIPTION_IDS` | Comma-separated subscription IDs to monitor | - |
| `DATABASE_URL` | SQLite connection string | `sqlite+aiosqlite:///./sla_data.db` |
| `COLLECTION_ENABLED` | Enable background data collection | `true` |
| `COLLECTION_INTERVAL_HOURS` | Hours between collection runs | `6` |
| `AZURE_STORAGE_ACCOUNT` | Storage account for DB backup (optional) | - |
| `AZURE_STORAGE_CONTAINER` | Blob container name for DB backup | `sla-backup` |
| `LOG_LEVEL` | Logging level | `INFO` |

### Manual Deployment (Docker)

```bash
# Using the deployment script
cd infra
chmod +x deploy.sh
./deploy.sh <resource-group> <subscription-ids>

# Or using Docker Compose locally
docker-compose up -d
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Web Dashboard                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │  Dashboard  │  │  Resources  │  │  Settings   │     │
│  └─────────────┘  └─────────────┘  └─────────────┘     │
└────────────────────────┬────────────────────────────────┘
                         │ FastAPI + Jinja2
┌────────────────────────┼────────────────────────────────┐
│                   API Layer                              │
│  /health  /ready  /api/dashboard  /api/resources        │
│  /api/metrics  /api/subscriptions  /api/collection      │
│  /api/export/csv                                         │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────┼────────────────────────────────┐
│              Background Scheduler                        │
│      (APScheduler - configurable interval)              │
│           + Blob Storage Backup                          │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────┼────────────────────────────────┐
│                  Core Modules                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ Discover │  │ Metrics  │  │Calculator│              │
│  └──────────┘  └──────────┘  └──────────┘              │
│       │              │              │                    │
│  Azure Resource  Azure Monitor  SLA Catalog             │
│     Graph                                                │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────┼────────────────────────────────┐
│              SQLite (async) + Blob Backup                │
│   Resources │ Metrics │ Subscriptions │ SLAHistory      │
└─────────────────────────────────────────────────────────┘
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Basic health check (always returns 200 if app is running) |
| `/ready` | GET | Readiness check (verifies database connectivity) |
| `/api/dashboard/summary` | GET | Dashboard summary statistics |
| `/api/resources` | GET | List resources with filtering and pagination |
| `/api/metrics` | GET | Query availability metrics |
| `/api/subscriptions` | GET/POST/DELETE | Manage monitored subscriptions |
| `/api/collection/trigger` | POST | Trigger manual data collection |
| `/api/export/csv` | GET | Download SLA data as CSV |
| `/api/docs` | GET | OpenAPI documentation (Swagger UI) |

## Development

```bash
# Install dev dependencies
uv sync --dev

# Run tests
pytest

# Type checking
mypy src/

# Linting
ruff check src/
```

## Limitations

- Metrics availability depends on Azure Monitor data retention (default 93 days)
- Some resource types have placeholder collectors (marked with TODO)
- Service Health correlation is stubbed pending stable SDK
- Availability calculations are estimates, not guaranteed SLA measurements

## License

MIT License - See [LICENSE](LICENSE)
