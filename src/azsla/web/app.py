"""FastAPI application factory."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from azsla.db.database import init_db, close_db
from azsla.web.config import get_settings
from azsla.web.scheduler import start_scheduler, stop_scheduler

logger = logging.getLogger(__name__)

# Application version
APP_VERSION = "0.1.0"

# Paths
WEB_DIR = Path(__file__).parent
STATIC_DIR = WEB_DIR / "static"
TEMPLATES_DIR = WEB_DIR / "templates"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan - startup and shutdown events."""
    settings = get_settings()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Restore database from blob storage if available
    try:
        from azsla.web.db_backup import restore_from_blob
        await restore_from_blob()
    except Exception as e:
        logger.warning(f"Blob restore skipped: {e}")

    # Initialize database
    logger.info("Initializing database...")
    await init_db()

    # Auto-register subscriptions from environment variable
    if settings.subscription_list:
        logger.info("Auto-registering subscriptions from environment...")
        from azsla.db.database import AsyncSessionLocal
        from azsla.db.repository import SubscriptionRepository
        
        # Try to fetch subscription names from Azure
        subscription_names = {}
        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.subscription import SubscriptionClient
            
            credential = DefaultAzureCredential()
            sub_client = SubscriptionClient(credential)
            
            for sub in sub_client.subscriptions.list():
                subscription_names[sub.subscription_id] = sub.display_name
            logger.info(f"Fetched names for {len(subscription_names)} subscriptions from Azure")
        except Exception as e:
            logger.warning(f"Could not fetch subscription names from Azure: {e}")
        
        async with AsyncSessionLocal() as session:
            sub_repo = SubscriptionRepository(session)
            for sub_id in settings.subscription_list:
                sub_id = sub_id.strip()
                name = subscription_names.get(sub_id)
                await sub_repo.upsert(sub_id, name=name)
            await session.commit()
            logger.info(f"Registered {len(settings.subscription_list)} subscriptions")

    # Start background scheduler
    if settings.collection_enabled:
        logger.info("Starting background scheduler...")
        await start_scheduler()

    yield

    # Shutdown
    logger.info("Shutting down...")
    await stop_scheduler()
    await close_db()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        description="Real-time Azure SLA monitoring and compliance dashboard",
        version=APP_VERSION,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        lifespan=lifespan,
    )

    # Create static directory if it doesn't exist
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    # Mount static files
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Register routers
    from azsla.web.api import router as api_router
    from azsla.web.views import router as views_router

    app.include_router(api_router, prefix="/api")
    app.include_router(views_router)

    # Health check endpoints
    @app.get("/health", tags=["Health"])
    async def health_check():
        """Basic health check - returns 200 if the app is running."""
        return JSONResponse({"status": "healthy", "version": APP_VERSION})

    @app.get("/ready", tags=["Health"])
    async def readiness_check():
        """Readiness check - verifies database connectivity."""
        try:
            from sqlalchemy import text
            from azsla.db.database import AsyncSessionLocal
            async with AsyncSessionLocal() as session:
                await session.execute(text("SELECT 1"))
            return JSONResponse({"status": "ready", "database": "connected"})
        except Exception as e:
            return JSONResponse(
                {"status": "not_ready", "database": "disconnected", "error": str(e)},
                status_code=503
            )

    return app


# Templates instance for use in views
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_templates() -> Jinja2Templates:
    """Get Jinja2 templates instance."""
    return templates
