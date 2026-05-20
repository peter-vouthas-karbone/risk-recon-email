#!/usr/bin/env python3
"""
Input Validator for PnL Pipeline.

This module handles validation of input files required for the PnL workflow.
It ensures all required files exist and are non-empty before processing begins.

The validator follows the Single Responsibility Principle by focusing solely
on input validation, separate from the main workflow orchestration.

Usage:
    >>> from pipeline.input_validator import InputValidator
    >>> validator = InputValidator(
    ...     rin_prices_path='/path/to/prices.csv',
    ...     rins_tradesheet_path='/path/to/trades.csv'
    ... )
    >>> result = validator.validate()
    >>> if result.is_valid:
    ...     print("All inputs valid")
"""

import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional

# Module logger
logger = logging.getLogger('pnl.' + __name__)


@dataclass
class ValidationResult:
    """
    Result container for input validation.

    Attributes:
        is_valid: Whether all required validation checks passed.
        tradesheet_files: List of valid tradesheet file paths.
        has_power_tradesheet: Whether a valid power tradesheet was found.
        errors: List of error messages for failed validations.
        warnings: List of warning messages for non-critical issues.
    """

    is_valid: bool
    tradesheet_files: List[str] = field(default_factory=list)
    has_power_tradesheet: bool = False
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class InputValidator:
    """
    Validates input files for the PnL pipeline.

    This class handles:
    - Checking file existence
    - Verifying files are non-empty
    - Categorizing required vs optional files
    - Collecting validation errors and warnings

    Attributes:
        rin_prices_path: Path to main RIN prices file (required).
        goo_prices_path: Path to GOO prices file (optional).
        boho_prices_path: Path to BOHO prices file (optional).
        rins_tradesheet_path: Path to RINs tradesheet (optional).
        management_tradesheet_path: Path to management tradesheet (optional).
        power_tradesheet_path: Path to power tradesheet (optional).
    """

    def __init__(
        self,
        rin_prices_path: str,
        goo_prices_path: Optional[str] = None,
        boho_prices_path: Optional[str] = None,
        rins_tradesheet_path: Optional[str] = None,
        management_tradesheet_path: Optional[str] = None,
        power_tradesheet_path: Optional[str] = None,
    ) -> None:
        """
        Initialize the validator with file paths.

        Args:
            rin_prices_path: Path to main RIN prices file (required).
            goo_prices_path: Path to GOO prices file (optional).
            boho_prices_path: Path to BOHO prices file (optional).
            rins_tradesheet_path: Path to RINs tradesheet (optional).
            management_tradesheet_path: Path to management tradesheet (optional).
            power_tradesheet_path: Path to power tradesheet (optional).
        """
        self.rin_prices_path = rin_prices_path
        self.goo_prices_path = goo_prices_path
        self.boho_prices_path = boho_prices_path
        self.rins_tradesheet_path = rins_tradesheet_path
        self.management_tradesheet_path = management_tradesheet_path
        self.power_tradesheet_path = power_tradesheet_path

    def validate(self) -> ValidationResult:
        """
        Validate all input files.

        Performs validation in order:
        1. Required RIN prices file
        2. Optional GOO prices file
        3. At least one tradesheet (RINs, management, or power)

        Returns:
            ValidationResult with validation status, file lists, and messages.
        """
        result = ValidationResult(is_valid=True)

        logger.info("Validating input files...")

        # Check prices file (always required)
        if not self._validate_file(self.rin_prices_path, is_required=True):
            result.is_valid = False
            result.errors.append(f"Missing or empty required file: {self.rin_prices_path}")
            return result

        # Check GOO prices file (optional)
        if self.goo_prices_path:
            if self._validate_file(self.goo_prices_path, is_required=False):
                logger.info("GOO prices file found and will be merged with main prices")
            else:
                result.warnings.append(
                    "No GOO prices file found - continuing with main prices only"
                )
                logger.info("No GOO prices file found - continuing with main prices only")

        # Check BOHO prices file (optional)
        if self.boho_prices_path:
            if self._validate_file(self.boho_prices_path, is_required=False):
                logger.info("BOHO prices file found and will be merged with main prices")
            else:
                result.warnings.append(
                    "No BOHO prices file found - continuing without BOHO prices"
                )
                logger.info("No BOHO prices file found - continuing without BOHO prices")

        # Check tradesheet files (at least one required)
        tradesheet_files: List[str] = []

        # Check RINs tradesheet (optional)
        if self.rins_tradesheet_path:
            if self._validate_file(self.rins_tradesheet_path, is_required=False):
                tradesheet_files.append(self.rins_tradesheet_path)

        # Check management tradesheet (optional)
        if self.management_tradesheet_path:
            if self._validate_file(self.management_tradesheet_path, is_required=False):
                tradesheet_files.append(self.management_tradesheet_path)

        # Check power tradesheet (optional)
        has_power = False
        if self.power_tradesheet_path:
            has_power = self._validate_file(self.power_tradesheet_path, is_required=False)

        if not tradesheet_files and not has_power:
            result.is_valid = False
            result.errors.append("No valid tradesheet files found")
            logger.error("No valid tradesheet files found")
            return result

        result.tradesheet_files = tradesheet_files
        result.has_power_tradesheet = has_power

        logger.info(f"Found {len(tradesheet_files)} valid fuels tradesheet file(s)")
        if has_power:
            logger.info("Found power tradesheet")

        return result

    def _validate_file(self, file_path: str, is_required: bool = True) -> bool:
        """
        Validate a single file for existence and non-emptiness.

        Args:
            file_path: Path to the file to validate.
            is_required: Whether the file is required (affects log level).

        Returns:
            True if file exists and is non-empty, False otherwise.
        """
        if not os.path.exists(file_path):
            if is_required:
                logger.error(f"Missing required file: {file_path}")
            else:
                logger.warning(f"File not found: {file_path}")
            return False

        file_size = os.path.getsize(file_path)
        if file_size == 0:
            if is_required:
                logger.error(f"File is empty: {file_path}")
            else:
                logger.warning(f"File is empty: {file_path}")
            return False

        logger.info(f"File size: {file_size:,} bytes - {file_path}")
        return True
