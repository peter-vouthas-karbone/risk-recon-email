#!/usr/bin/env python3
"""
Centralized logging configuration for PnL pipeline.

This module provides a single point of configuration for all logging across
the application. It uses a named 'pnl' logger (not root) to avoid side effects
on imported libraries and other code.

Usage:
    # In entry points (run_pnl_pipeline.py, etc.)
    from logging_utils import init_logging
    run_id = init_logging(log_prefix='pnl')
    
    # In all other modules
    import logging
    logger = logging.getLogger('pnl.' + __name__)
    logger.info("Module loaded")
"""

import logging
import logging.config
import os
from datetime import datetime
from typing import Optional

from karbone_pnl_pos.utils.config_loader import get_config
from karbone_pnl_pos.utils import constants


_logging_initialized = False
_current_run_id: Optional[str] = None


class RunIDFilter(logging.Filter):
    """Inject run_id into log records."""
    
    def __init__(self, run_id: str):
        super().__init__()
        self.run_id = run_id
    
    def filter(self, record):
        record.run_id = self.run_id
        return True


_DEFAULT_CONSOLE_FORMAT = '[%(asctime)s] %(levelname)s: %(message)s'
_DEFAULT_FILE_FORMAT = '%(asctime)s [%(levelname)-8s] [%(run_id)s] %(name)s: %(message)s'


def build_logging_config(
    log_file_path: str,
    run_id: str,
    console_level: str = 'INFO',
    file_level: str = 'INFO',
    console_format: str = _DEFAULT_CONSOLE_FORMAT,
    file_format: str = _DEFAULT_FILE_FORMAT,
) -> dict:
    """Build dictConfig for logging setup."""
    return {
        'version': 1,
        'disable_existing_loggers': False,  # Don't silence third-party libraries
        'formatters': {
            'console': {
                'format': console_format,
                'datefmt': '%Y-%m-%d %H:%M:%S'
            },
            'file': {
                'format': file_format,
                'datefmt': '%Y-%m-%d %H:%M:%S'
            }
        },
        'filters': {
            'run_id': {
                '()': RunIDFilter,
                'run_id': run_id
            }
        },
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
                'level': console_level,
                'formatter': 'console',
                'stream': 'ext://sys.stdout'
            },
            'file': {
                'class': 'logging.handlers.RotatingFileHandler',
                'level': file_level,
                'formatter': 'file',
                'filename': log_file_path,
                'maxBytes': 10485760,  # 10MB
                'backupCount': 5,
                'filters': ['run_id']
            }
        },
        'loggers': {
            'pnl': {
                'handlers': ['console', 'file'],
                'level': 'DEBUG',  # Capture all, filter via handlers
                'propagate': False  # Don't send to root logger
            }
        }
    }


def init_logging(
    log_prefix: str = 'pnl',
    console_level: Optional[str] = None,
    file_level: Optional[str] = None
) -> str:
    """
    Initialize logging for the application.
    
    Should be called once at the start of each entry point. Subsequent calls
    are idempotent and return the same run_id.
    
    Args:
        log_prefix: Prefix for log filename (e.g., 'pnl', 'pos', 'scheduler')
        console_level: Console log level (default: from config or INFO)
        file_level: File log level (default: from config or INFO)
    
    Returns:
        run_id: Unique run identifier (timestamp string)
    
    Raises:
        OSError: If log directory cannot be created
    
    Example:
        >>> from logging_utils import init_logging
        >>> run_id = init_logging(log_prefix='pnl')
        >>> logger = logging.getLogger('pnl.my_module')
        >>> logger.info("Started processing")
    """
    global _logging_initialized, _current_run_id

    if _logging_initialized:
        return _current_run_id or ""

    config = get_config()
    logging_config = config.get('logging', {})

    run_id = datetime.now().strftime(constants.DATE_FORMAT_TIMESTAMP)
    _current_run_id = run_id

    results_dir = config.get_path('results')
    log_dir = os.path.join(results_dir, 'logs')
    os.makedirs(log_dir, exist_ok=True)

    log_file_path = os.path.join(log_dir, f"{log_prefix}_{run_id}.log")

    console_level_str = console_level or logging_config.get('console_level', 'INFO')
    file_level_str = file_level or logging_config.get('file_level', 'INFO')
    console_format = logging_config.get('console_format', _DEFAULT_CONSOLE_FORMAT)
    file_format = logging_config.get('file_format', _DEFAULT_FILE_FORMAT)

    config_dict = build_logging_config(
        log_file_path=log_file_path,
        run_id=run_id,
        console_level=console_level_str,
        file_level=file_level_str,
        console_format=console_format,
        file_format=file_format,
    )

    logging.config.dictConfig(config_dict)

    _logging_initialized = True

    logger = logging.getLogger('pnl.logging_utils')
    logger.info("Logging initialized: run_id=%s", run_id)
    logger.info("Log file: %s", log_file_path)
    logger.debug("Console level: %s, File level: %s", console_level_str, file_level_str)

    return run_id


def get_run_id() -> Optional[str]:
    """
    Get the current run_id.
    
    Returns:
        Current run_id if logging initialized, None otherwise
    """
    return _current_run_id


def reset_logging() -> None:
    """
    Reset logging state (for testing only).
    
    Clears all handlers and resets initialization flag.
    """
    global _logging_initialized, _current_run_id

    pnl_logger = logging.getLogger('pnl')
    pnl_logger.handlers.clear()
    
    _logging_initialized = False
    _current_run_id = None

