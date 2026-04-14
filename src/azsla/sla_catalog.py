"""SLA catalog management - maps resource types to SLA targets."""

import logging
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from azsla.models import ResourceRecord, SLATarget

logger = logging.getLogger(__name__)


def _find_catalog_path() -> Path:
    """Find the SLA catalog path in multiple locations."""
    # List of possible locations
    candidates = [
        # Development mode (relative to source)
        Path(__file__).parent.parent.parent / "slas" / "azure_sla_catalog.yaml",
        # Installed in share directory
        Path(sys.prefix) / "share" / "azsla" / "slas" / "azure_sla_catalog.yaml",
        # Docker container /app/slas
        Path("/app/slas/azure_sla_catalog.yaml"),
        # Working directory
        Path.cwd() / "slas" / "azure_sla_catalog.yaml",
        # Environment variable override
        Path(os.environ.get("AZSLA_CATALOG_PATH", "/nonexistent")),
    ]
    
    for path in candidates:
        if path.exists():
            return path
    
    # Return first candidate as fallback (will trigger warning in load)
    return candidates[0]


# Default SLA catalog path
DEFAULT_CATALOG_PATH = _find_catalog_path()

# Fallback SLA for unknown resource types
UNKNOWN_SLA = SLATarget(
    resource_type="unknown",
    sla_percent=-1,
    conditions={},
    source="SLA_UNKNOWN",
)


class SLACatalog:
    """Manages SLA targets for Azure resource types."""

    def __init__(self, catalog_path: Path | str | None = None):
        """
        Initialize the SLA catalog.

        Args:
            catalog_path: Path to YAML catalog file. Uses default if not provided.
        """
        self.catalog_path = Path(catalog_path) if catalog_path else DEFAULT_CATALOG_PATH
        self._catalog: dict[str, Any] = {}
        self._load_catalog()

    def _load_catalog(self) -> None:
        """Load the SLA catalog from YAML."""
        if not self.catalog_path.exists():
            logger.warning(f"SLA catalog not found: {self.catalog_path}")
            self._catalog = {"resource_types": {}, "default_sla": 99.9}
            return

        try:
            with open(self.catalog_path) as f:
                self._catalog = yaml.safe_load(f) or {}
            logger.info(f"Loaded SLA catalog from {self.catalog_path}")
        except Exception as e:
            logger.error(f"Failed to load SLA catalog: {e}")
            self._catalog = {"resource_types": {}, "default_sla": 99.9}

    def get_sla(self, resource: ResourceRecord) -> SLATarget:
        """
        Get the SLA target for a resource.

        Considers resource type, SKU, and tier when matching.

        Args:
            resource: The resource to look up

        Returns:
            SLATarget with the applicable SLA
        """
        resource_types = self._catalog.get("resource_types", {})
        resource_type_lower = resource.type.lower()

        # Find matching resource type entry
        type_config = None
        for rt, config in resource_types.items():
            if rt.lower() == resource_type_lower:
                type_config = config
                break

        if type_config is None:
            # Check for default SLA
            default_sla = self._catalog.get("default_sla")
            if default_sla is not None:
                return SLATarget(
                    resource_type=resource.type,
                    sla_percent=float(default_sla),
                    source="catalog_default",
                )
            return UNKNOWN_SLA

        # Check conditions first (more specific matches)
        conditions = type_config.get("conditions", [])
        for condition in conditions:
            if self._matches_condition(resource, condition):
                return SLATarget(
                    resource_type=resource.type,
                    sla_percent=float(condition.get("sla", type_config.get("default_sla", 99.9))),
                    conditions=condition,
                    source="catalog_condition",
                )

        # Use default SLA for this type
        default_type_sla = type_config.get("default_sla")
        if default_type_sla is not None:
            return SLATarget(
                resource_type=resource.type,
                sla_percent=float(default_type_sla),
                source="catalog_type_default",
            )

        return UNKNOWN_SLA

    def _matches_condition(self, resource: ResourceRecord, condition: dict[str, Any]) -> bool:
        """Check if a resource matches a condition."""
        # SKU contains check
        if "sku_contains" in condition:
            if not resource.sku or condition["sku_contains"].lower() not in resource.sku.lower():
                return False

        # Exact SKU match
        if "sku" in condition:
            if not resource.sku or resource.sku.lower() != condition["sku"].lower():
                return False

        # Tier check
        if "tier" in condition:
            if not resource.tier or resource.tier.lower() != condition["tier"].lower():
                return False

        # Tier contains check
        if "tier_contains" in condition:
            if not resource.tier or condition["tier_contains"].lower() not in resource.tier.lower():
                return False

        # Availability set check (for VMs)
        if "availability_set" in condition:
            has_avset = bool(resource.properties.get("availabilitySet"))
            if has_avset != condition["availability_set"]:
                return False

        # Availability zone check
        if "availability_zones" in condition:
            zones = resource.properties.get("zones", [])
            if condition["availability_zones"] and not zones:
                return False
            if not condition["availability_zones"] and zones:
                return False

        return True

    def list_supported_types(self) -> list[str]:
        """List all resource types in the catalog."""
        return list(self._catalog.get("resource_types", {}).keys())

    def reload(self) -> None:
        """Reload the catalog from disk."""
        self._load_catalog()


# Module-level catalog instance
_catalog: SLACatalog | None = None


def get_catalog(catalog_path: Path | str | None = None) -> SLACatalog:
    """Get or create the SLA catalog instance."""
    global _catalog
    if _catalog is None or catalog_path is not None:
        _catalog = SLACatalog(catalog_path)
    return _catalog


def get_sla_for_resource(
    resource: ResourceRecord,
    catalog_path: Path | str | None = None,
) -> SLATarget:
    """Convenience function to get SLA for a resource."""
    catalog = get_catalog(catalog_path)
    return catalog.get_sla(resource)
