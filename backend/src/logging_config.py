"""Centralized logging configuration with rotation."""
import logging
import logging.handlers
import sys

from .paths import get_logs_dir

LOGS_DIR = get_logs_dir()


def setup_logging(name: str = None, level: str = "INFO"):
    """
    Setup logging with rotation and multiple handlers.

    Creates:
    - logs/extraction.log: All extraction-related logs (10MB, 5 backups)
    - logs/api.log: API server logs
    - logs/errors.log: ERROR+ only
    - Console: INFO+ with color

    Args:
        name: Logger name (e.g., 'extraction', 'api')
        level: Logging level (DEBUG, INFO, WARNING, ERROR)

    Returns:
        Logger instance
    """

    # Root logger
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Clear existing handlers
    root.handlers = []

    # Console handler (INFO+)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console_fmt = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%H:%M:%S'
    )
    console.setFormatter(console_fmt)
    root.addHandler(console)

    # Extraction file handler (DEBUG+, rotating)
    extraction_file = logging.handlers.RotatingFileHandler(
        LOGS_DIR / "extraction.log",
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    extraction_file.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        '%(asctime)s [%(levelname)s] [%(name)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    extraction_file.setFormatter(file_fmt)
    root.addHandler(extraction_file)

    # Error file handler (ERROR+ only)
    error_file = logging.handlers.RotatingFileHandler(
        LOGS_DIR / "errors.log",
        maxBytes=5*1024*1024,  # 5MB
        backupCount=3,
        encoding="utf-8",
    )
    error_file.setLevel(logging.ERROR)
    error_file.setFormatter(file_fmt)
    root.addHandler(error_file)

    # API file handler (if running API server)
    if name == 'api':
        api_file = logging.handlers.RotatingFileHandler(
            LOGS_DIR / "api.log",
            maxBytes=10*1024*1024,
            backupCount=5,
            encoding="utf-8",
        )
        api_file.setLevel(logging.DEBUG)
        api_file.setFormatter(file_fmt)
        root.addHandler(api_file)

    logger = logging.getLogger(name or __name__)
    logger.info(f"Logging initialized. Logs directory: {LOGS_DIR}")
    return logger
