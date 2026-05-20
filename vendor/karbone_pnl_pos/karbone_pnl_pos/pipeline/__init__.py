#!/usr/bin/env python3
"""
Pipeline Package.

This package contains the orchestration components for the PnL pipeline,
providing modular, single-responsibility classes for input validation
and email notifications.

Modules:
    input_validator: Validates input files before processing.
    email_sender: Sends email notifications for reports.

Usage:
    >>> from pipeline import InputValidator, EmailSender
    >>> validator = InputValidator(
    ...     rin_prices_path='/path/to/prices.csv',
    ...     rins_tradesheet_path='/path/to/trades.csv'
    ... )
    >>> result = validator.validate()
"""

from .email_sender import EmailSender
from .input_validator import InputValidator

__all__ = [
    "InputValidator",
    "EmailSender",
]
