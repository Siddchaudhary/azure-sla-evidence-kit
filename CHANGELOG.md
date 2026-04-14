# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-04-14

### Added
- **Dark Mode**: Toggle between light and dark themes
  - Sun/moon icon in header
  - Persists preference in localStorage
  - Full dark mode support across all components
- **Animated Counters**: Numbers count up on page load for stats cards
  - Compliance percentage, resource counts
  - Smooth easing animation
- **Chart.js Donut Chart**: Animated donut for compliance distribution
  - Replaces static SVG donut
  - Hover tooltips with percentages
- **Toast Notifications**: Slide-in alerts for user actions
  - Success/error/warning/info variants
  - Auto-dismiss with manual close option
- **Progress Bar Animations**: Compliance bar animates on load
- **Hover Effects**: Cards now have hover shadow transitions

### Changed
- Improved dark mode styling for all UI components
- Better visual feedback on interactive elements

## [0.2.0] - 2026-04-14

### Added
- **Unit Tests**: Comprehensive test suite with pytest-asyncio
  - `tests/conftest.py` - Shared fixtures for async testing
  - `tests/test_api.py` - API endpoint tests (health, collection, export)
  - `tests/test_repository.py` - Repository layer tests
- **API Rate Limiting**: Optional rate limiting middleware using slowapi
  - Configurable limits per endpoint
  - Graceful degradation when slowapi is not installed
- **Streaming CSV Export**: Memory-efficient CSV export for large datasets
  - Batched row generation (100 rows per chunk)
  - Proper streaming response headers
- **Optional Dependencies**:
  - `[postgres]` extra for PostgreSQL support with asyncpg
  - `[ratelimit]` extra for slowapi rate limiting
  - `[charts]` extra for matplotlib chart generation

### Fixed
- Resource count now shows unique resources instead of cumulative metrics

### Changed
- Moved slowapi from required to optional dependencies
- CSV export endpoint now uses streaming response

## [0.1.0] - 2026-04-14

### Added
- Initial release
- Azure Portal-style web dashboard
- Resource auto-discovery via Azure Resource Graph
- Azure Monitor metrics collection
- SLA compliance calculation and reporting
- 30-day trend charts with historical snapshots
- CSV export functionality
- Background data collection with APScheduler
- SQLite persistence with Azure Blob Storage backup
- Health endpoints (`/health`, `/ready`) for container orchestration
- Azure Container Apps deployment support
- Managed identity authentication
- CLI commands for report generation
- Support for 20+ Azure resource types

### Resource Types Supported
- Virtual Machines, App Services, Function Apps
- AKS Clusters, SQL Databases, Storage Accounts
- PostgreSQL Flexible Servers, Container Apps
- Load Balancers, Application Gateways
- Key Vault, Cosmos DB, Redis Cache
- Service Bus, Event Hubs
- Front Door/CDN, Bastion Hosts, VNet Gateways
- Azure Firewall, Cognitive Services
- ExpressRoute, Public IPs
