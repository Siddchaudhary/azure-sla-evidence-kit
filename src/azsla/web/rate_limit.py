"""Rate limiting middleware for API protection."""

import logging
from typing import Callable

from fastapi import FastAPI, Request, Response
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)


def get_client_ip(request: Request) -> str:
    """Get client IP address, handling proxy headers."""
    # Check for forwarded headers (common in container environments)
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # Take the first IP in the chain (original client)
        return forwarded.split(",")[0].strip()
    
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    
    # Fall back to direct connection IP
    return get_remote_address(request)


# Create limiter instance with custom key function
limiter = Limiter(
    key_func=get_client_ip,
    default_limits=["200/minute", "1000/hour"],
    storage_uri="memory://",  # Use in-memory storage (consider Redis for production)
    strategy="fixed-window",
)


def setup_rate_limiting(app: FastAPI) -> None:
    """Configure rate limiting for the FastAPI application.
    
    Rate limits:
    - Default: 200 requests per minute, 1000 per hour
    - Collection trigger: 2 per minute (expensive operation)
    - Export CSV: 10 per minute (resource intensive)
    - Health checks: No limit (needed for orchestration)
    """
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    
    logger.info("Rate limiting enabled: 200/min, 1000/hour default")


# Decorator for specific rate limits
def rate_limit(limit: str) -> Callable:
    """Decorator to apply custom rate limit to an endpoint.
    
    Usage:
        @router.post("/expensive-operation")
        @rate_limit("5/minute")
        async def expensive_operation():
            ...
    """
    return limiter.limit(limit)


# Pre-configured decorators for common limits
rate_limit_collection = limiter.limit("2/minute")  # Trigger collection
rate_limit_export = limiter.limit("10/minute")  # CSV export
rate_limit_write = limiter.limit("30/minute")  # Write operations
