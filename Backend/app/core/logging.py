"""
Centralised logging configuration for the application.
Call `setup_logging()` once at startup (in main.py lifespan).
"""

import logging
import sys

from app.core.config import settings


def setup_logging() -> None:
    """Configure root logger with a structured format."""
    log_format = (
        "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s"
    )
    logging.basicConfig(
        level=settings.LOG_LEVEL.upper(),
        format=log_format,
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )
    # Silence noisy third-party loggers in production
    if settings.ENVIRONMENT != "development":
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a named child logger."""
    return logging.getLogger(name)
