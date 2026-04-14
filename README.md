# Azure SLA Report Generator

A Python CLI and **web dashboard** that discovers Azure resources, collects availability metrics, and generates SLA compliance reports in real-time.

> ⚠️ **Disclaimer**: This tool provides informational availability estimates based on Azure Monitor metrics. It is **not** an official SLA attestation tool and should not be used for contractual SLA claims. Always refer to the Azure Portal SLA credit process for official SLA breach claims.

## Features

- 🔍 **Discovery**: Enumerate running resources across multiple subscriptions using Azure Resource Graph
- 📊 **Metrics Collection**: Pull availability signals from Azure Monitor
- 📋 **SLA Catalog**: Map resource types to published Azure SLAs (configurable YAML)
- 🧮 **Compliance Calculation**: Compare actual uptime vs SLA targets
- 📝 **Reports**: Executive summary (Markdown), detailed report (HTML), CSV exports
- 🌐 **Web Dashboard**: Real-time SLA monitoring with date range selection
- ⏰ **Background Collection**: Automatic periodic data collection
- ☁️ **Azure-Ready**: Deploy to Azure Container Apps with managed identity

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

This tool requires **read-only** access. Assign the **Reader** role at the subscription or management group level:

```bash
az role assignment create \
  --assignee <service-principal-id> \
  --role "Reader" \
  --scope /subscriptions/<subscription-id>
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

1. Create an Azure AD App Registration with federated credentials for GitHub Actions
2. Set repository secrets:
   - `AZURE_CLIENT_ID`
   - `AZURE_TENANT_ID`
   - `AZURE_SUBSCRIPTION_ID`
   - `MONITORED_SUBSCRIPTION_IDS` (comma-separated list)
3. Set repository variables:
   - `AZURE_RESOURCE_GROUP`
4. Push to `main` branch or manually trigger the workflow

### Manual Deployment

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
                         │ FastAPI
┌────────────────────────┼────────────────────────────────┐
│                   API Layer                              │
│  /api/dashboard  /api/resources  /api/metrics           │
│  /api/subscriptions  /api/collection                    │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────┼────────────────────────────────┐
│              Background Scheduler                        │
│         (APScheduler - runs every 6 hours)              │
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
│              SQLite / PostgreSQL                         │
│   Resources │ Metrics │ Subscriptions │ CollectionRuns  │
└─────────────────────────────────────────────────────────┘
```

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
