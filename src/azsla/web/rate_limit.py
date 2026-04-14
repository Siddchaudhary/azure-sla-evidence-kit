"""Rate limiting middleware for API protection."""

import logging
import os
from typing import Callable, Optional

from fastapi import FastAPI, Request

logger = logging.getLogger(__name__)

# Rate limiting is optional - only enabled if slowapi is installed
RATE_LIMITING_ENABLED = os.getenv("RATE_LIMITING_ENABLED", "true").lower() == "true"

try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from slowapi.util import get_remote_address
    SLOWAPI_AVAILABLE = True
except ImportError:
    SLOWAPI_AVAILABLE = False
    logger.warning("slowapi not installed - rate limiting disabled")


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
    if SLOWAPI_AVAILABLE:
        return get_remote_address(request)
    return request.client.host if request.client else "unknown"


# Create limiter instance if slowapi is available
if SLOWAPI_AVAILABLE and RATE_LIMITING_ENABLED:
    limiter = Limiter(
        key_func=get_client_ip,
        default_limits=["200/minute", "1000/hour"],
        storage_uri="memory://",
        strategy="fixed-window",
    )
else:
    limiter = None


def setup_rate_limiting(app: FastAPI) -> None:
    """Configure rate limiting for the FastAPI application."""
    if not SLOWAPI_AVAILABLE:
        logger.info("Rate limiting not available (slowapi not installed)")
        return
    
    if not RATE_LIMITING_ENABLED:
        logger.info("Rate limiting disabled by configuration")
        return
    
    if limiter:
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        logger.info("Rate limiting enabled: 200/min, 1000/hour default")


def rate_limit(limit: str) -> Callable:
    """Decorator to apply custom rate limit to an endpoint."""
    if limiter:
        return limiter.limit(limit)
    # Return a no-op decorator if rate limiting is disabled
    def noop_decorator(func):
        return func
    return noop_decorator


# Pre-configured decorators for common limits
def _get_limit_decorator(limit: str) -> Callable:
    """Get a rate limit decorator or no-op if disabled."""
    if limiter:
        return limiter.limit(limit)
    def noop_decorator(func):
        return func
    return noop_decorator

rate_limit_collection = _get_limit_decorator("2/minute")
rate_limit_export = _get_limit_decorator("10/minute")
rate_limit_write = _get_limit_decorator("30/minute")
