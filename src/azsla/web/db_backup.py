"""Database backup/restore to Azure Blob Storage for persistence across container restarts."""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Blob storage configuration
STORAGE_ACCOUNT_NAME = os.getenv("AZURE_STORAGE_ACCOUNT", "")
STORAGE_CONTAINER = os.getenv("AZURE_STORAGE_CONTAINER", "sla-data")
BLOB_NAME = "sla_data.db"


def get_local_db_path() -> Optional[Path]:
    """Get the local SQLite database path from DATABASE_URL."""
    db_url = os.getenv("DATABASE_URL", "")
    if "sqlite" not in db_url:
        return None
    # Extract path from sqlite+aiosqlite:///path/to/db
    path = db_url.split("///")[-1]
    return Path(path) if path else None


async def restore_from_blob() -> bool:
    """
    Download SQLite database from Azure Blob Storage on startup.
    Returns True if successfully restored, False otherwise.
    """
    if not STORAGE_ACCOUNT_NAME:
        logger.debug("No storage account configured, skipping blob restore")
        return False

    db_path = get_local_db_path()
    if not db_path:
        logger.debug("Not using SQLite, skipping blob restore")
        return False

    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        logger.info(f"Attempting to restore database from blob storage...")

        credential = DefaultAzureCredential()
        blob_service = BlobServiceClient(
            account_url=f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net",
            credential=credential,
        )

        container_client = blob_service.get_container_client(STORAGE_CONTAINER)

        # Check if blob exists
        blob_client = container_client.get_blob_client(BLOB_NAME)
        if not blob_client.exists():
            logger.info("No backup found in blob storage, starting fresh")
            return False

        # Ensure directory exists
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # Download the database
        with open(db_path, "wb") as f:
            download_stream = blob_client.download_blob()
            f.write(download_stream.readall())

        file_size = db_path.stat().st_size
        logger.info(f"Successfully restored database from blob ({file_size} bytes)")
        return True

    except ImportError:
        logger.warning("azure-storage-blob not installed, skipping blob restore")
        return False
    except Exception as e:
        logger.warning(f"Failed to restore from blob storage: {e}")
        return False


async def backup_to_blob() -> bool:
    """
    Upload SQLite database to Azure Blob Storage after collection.
    Returns True if successfully backed up, False otherwise.
    """
    if not STORAGE_ACCOUNT_NAME:
        logger.debug("No storage account configured, skipping blob backup")
        return False

    db_path = get_local_db_path()
    if not db_path or not db_path.exists():
        logger.debug("No SQLite database to backup")
        return False

    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        logger.info("Backing up database to blob storage...")

        credential = DefaultAzureCredential()
        blob_service = BlobServiceClient(
            account_url=f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net",
            credential=credential,
        )

        container_client = blob_service.get_container_client(STORAGE_CONTAINER)

        # Ensure container exists
        try:
            container_client.create_container()
            logger.info(f"Created container: {STORAGE_CONTAINER}")
        except Exception:
            pass  # Container already exists

        # Upload the database
        blob_client = container_client.get_blob_client(BLOB_NAME)
        with open(db_path, "rb") as f:
            blob_client.upload_blob(f, overwrite=True)

        file_size = db_path.stat().st_size
        logger.info(f"Successfully backed up database to blob ({file_size} bytes)")
        return True

    except ImportError:
        logger.warning("azure-storage-blob not installed, skipping blob backup")
        return False
    except Exception as e:
        logger.warning(f"Failed to backup to blob storage: {e}")
        return False
