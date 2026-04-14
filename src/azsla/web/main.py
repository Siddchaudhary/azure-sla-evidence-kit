"""Main entry point for the web application."""

import uvicorn

from azsla.web.app import create_app
from azsla.web.config import get_settings


def run() -> None:
    """Run the web application."""
    settings = get_settings()
    app = create_app()

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
