#!/usr/bin/env python3
"""
Configuration Loader for RINs PnL Analysis System.

This module provides a centralized configuration management system that loads
settings from a YAML file and provides convenient access to configuration values
throughout the application. It uses a singleton pattern for efficient reuse.

Example:
    >>> from config_loader import get_config, get_min_trade_date
    >>> config = get_config()
    >>> min_date = get_min_trade_date()
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import yaml
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

# Determine project root to locate config file
PROJECT_ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_FILE: str = os.path.join(PROJECT_ROOT, 'config', 'settings.yaml')

# Module logger
logger = logging.getLogger('pnl.' + __name__)

# Config keys that represent input files and may be stored in GCS
GCS_INPUT_PATH_KEYS = {
    'rins_tradesheet',
    'management_rins_tradesheet',
    'management_power_tradesheet',
    'power_tradesheet',
    'nyiso_bid_records',
    'pjm_bid_records',
    'miso_bid_records',
    'caiso_bid_records',
    'rin_prices',
    'boho_prices',
    'goo_prices',
    'nyiso_spot_pricing',
    'pjm_spot_pricing',
    'miso_spot_pricing',
    'caiso_spot_pricing',
    'pjm_forward_curve',
    'nyiso_forward_curve',
    'ercot_forward_curve',
    'ercot_ancillary_prices',
    'nepool_forward_curve',
    'caiso_forward_curve',
    'miso_forward_curve',
    'gas_forward_curve',
    'additional_trades',
    'fee_schedule',
}


def _is_gcs_path(path: str) -> bool:
    return isinstance(path, str) and path.startswith('gs://')


def _parse_gcs_uri(uri: str) -> Tuple[str, str]:
    """
    Parse a GCS URI into bucket and object name.
    """
    without_scheme = uri[5:]
    bucket, _, blob = without_scheme.partition('/')
    if not bucket or not blob:
        raise ValueError(f"Invalid GCS URI: {uri}")
    return bucket, blob


class ConfigLoader:
    """
    Loads and provides access to configuration settings from a YAML file.

    This class handles reading configuration from a YAML file, resolving relative
    paths to absolute paths, and providing convenient accessor methods for
    different configuration sections.

    Attributes:
        config_file: Path to the configuration YAML file.
        config: Dictionary containing the loaded configuration.
    """

    def __init__(self, config_file: Optional[str] = None) -> None:
        """
        Initialize configuration loader.

        Args:
            config_file: Path to configuration file. If None, uses the default
                        location at `config/settings.yaml`.

        Raises:
            FileNotFoundError: If the configuration file does not exist.
            ValueError: If the configuration file is empty or contains invalid YAML.
        """
        self.config_file: str = config_file or CONFIG_FILE
        self.config: Dict[str, Any] = self._load_config()
        self._gcs_cache_dir: str = os.getenv(
            'GCS_CACHE_DIR',
            os.path.join(PROJECT_ROOT, '.gcs_cache')
        )
        self._gcs_cache_map: Dict[str, str] = {}
        self._force_gcs_refresh: bool = os.getenv('GCS_FORCE_REFRESH', '').strip().lower() in {'1', 'true', 'yes'}
        self._resolve_paths()

    def _load_config(self) -> Dict[str, Any]:
        """
        Load configuration from YAML file.

        Returns:
            Dictionary containing the parsed configuration.

        Raises:
            FileNotFoundError: If the configuration file does not exist.
            ValueError: If the configuration file is empty or contains invalid YAML.
        """
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                if config is None:
                    raise ValueError("Configuration file is empty")
                return config
        except FileNotFoundError:
            raise FileNotFoundError(f"Configuration file not found: {self.config_file}")
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in configuration file: {e}")

    def _resolve_paths(self) -> None:
        """
        Convert relative paths to absolute paths based on project root.

        This method iterates through all paths defined in the 'paths' section
        of the configuration and converts any relative paths to absolute paths
        by prepending the project root directory.

        On non-Windows platforms, Windows-style shared drive paths
        (e.g. ``G:\\Shared drives\\...``) are rewritten using the
        ``SHARED_DRIVE_ROOT`` environment variable.  Set it to the local
        equivalent of ``G:\\Shared drives``, e.g.:

            export SHARED_DRIVE_ROOT="/Users/you/Library/CloudStorage/GoogleDrive-you@example.com/Shared drives"
        """
        if 'paths' not in self.config:
            return

        shared_drive_root = os.getenv('SHARED_DRIVE_ROOT', '').rstrip('/\\')
        windows_shared_drive_re = re.compile(r'^[A-Za-z]:\\Shared drives\\', re.IGNORECASE)

        for key, path in self.config['paths'].items():
            if not isinstance(path, str):
                continue
            if windows_shared_drive_re.match(path):
                if shared_drive_root:
                    path = windows_shared_drive_re.sub(shared_drive_root + os.sep, path)
                    path = path.replace('\\', os.sep)
                    self.config['paths'][key] = path
            elif not os.path.isabs(path) and not _is_gcs_path(path):
                self.config['paths'][key] = os.path.join(PROJECT_ROOT, path)

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value using dot notation.

        Args:
            key: Configuration key in dot notation (e.g., 'paths.rins_tradesheet').
            default: Default value to return if key is not found.

        Returns:
            The configuration value, or the default if not found.

        Example:
            >>> config.get('pnl_algorithm.forward_fill_allowed', True)
            True
        """
        keys = key.split('.')
        value: Any = self.config

        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default

        return value

    def get_path(self, path_key: str) -> str:
        """
        Get an absolute file path from the paths configuration section.

        Args:
            path_key: Key name within the 'paths' section.

        Returns:
            Absolute file path, or empty string if not found.
        """
        path = self.get(f'paths.{path_key}', '')
        if path_key in GCS_INPUT_PATH_KEYS and _is_gcs_path(path):
            return self._materialize_gcs_path(path)
        return path

    def _materialize_gcs_path(self, gcs_uri: str) -> str:
        """
        Download a GCS object to a local cache directory and return the local path.
        """
        if gcs_uri in self._gcs_cache_map:
            return self._gcs_cache_map[gcs_uri]

        bucket_name, blob_name = _parse_gcs_uri(gcs_uri)
        local_path = os.path.join(self._gcs_cache_dir, bucket_name, *blob_name.split('/'))
        local_dir = os.path.dirname(local_path)
        os.makedirs(local_dir, exist_ok=True)

        try:
            cached_size = os.stat(local_path).st_size
        except OSError:
            cached_size = 0
        if cached_size > 0 and not self._force_gcs_refresh:
            self._gcs_cache_map[gcs_uri] = local_path
            return local_path

        try:
            from google.cloud import storage  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "GCS path detected but google-cloud-storage is not installed. "
                "Install dependencies from requirements.txt."
            ) from exc

        logger.info("Downloading GCS file: %s", gcs_uri)
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        try:
            blob.download_to_filename(local_path)
        except Exception as exc:
            if 'NotFound' in type(exc).__name__ or '404' in str(exc):
                raise FileNotFoundError(f"GCS object not found: {gcs_uri}") from exc
            raise
        self._gcs_cache_map[gcs_uri] = local_path
        return local_path

    def get_pnl_config(self) -> Dict[str, Any]:
        """
        Get PnL algorithm configuration as a dictionary.

        Returns:
            Dictionary containing PnL algorithm settings including:
            - forward_fill_allowed: Whether to forward-fill missing prices.
            - missing_pnl_policy: Policy for handling missing PnL ('null' or 'zero').
            - enable_vintage_modeling: Whether to model missing vintage prices.
            - use_trade_price_fallback: Whether to use trade prices as fallback marks.
        """
        return self.get('pnl_algorithm', {})

    def get_conversion_config(self) -> Dict[str, Any]:
        """
        Get conversion configuration as a dictionary.

        Returns:
            Dictionary containing conversion settings.
        """
        return self.get('conversion', {})

    def get_precision_config(self) -> Dict[str, int]:
        """
        Get precision configuration for rounding numeric outputs.

        Returns:
            Dictionary mapping value types to decimal places, e.g.:
            {'prices': 5, 'pnl': 2, 'quantities': 2, 'countervalues': 2}
        """
        return self.get('processing.precision', {})

    def get_template(self, template_key: str) -> str:
        """
        Get a filename template from the templates configuration section.

        Args:
            template_key: Key name within the 'templates' section.

        Returns:
            Template string with placeholders (e.g., 'daily_pnl_{timestamp}.csv').
        """
        return self.get(f'templates.{template_key}', '')


# Global configuration instance (singleton pattern)
_config_instance: Optional[ConfigLoader] = None


def get_config() -> ConfigLoader:
    """
    Get the global configuration instance (singleton pattern).

    Returns:
        The global ConfigLoader instance.

    Example:
        >>> config = get_config()
        >>> path = config.get_path('rin_prices')
    """
    global _config_instance
    if _config_instance is None:
        _config_instance = ConfigLoader()
    return _config_instance


def reload_config() -> ConfigLoader:
    """
    Reload configuration from file, replacing the global instance.

    This is useful for testing or when configuration files are updated
    during runtime.

    Returns:
        The newly loaded ConfigLoader instance.
    """
    global _config_instance
    _config_instance = ConfigLoader()
    return _config_instance


# =============================================================================
# Convenience Functions for Common Configuration Access
# =============================================================================


def get_min_trade_date() -> str:
    """
    Get the minimum trade date for filtering.

    Returns:
        Date string in YYYY-MM-DD format.
    """
    return get_config().get('dates.min_trade_date', '2024-06-21')


def get_broker_skip_phrases() -> List[str]:
    """
    Get phrases that trigger skipping rows for broker notes.

    Returns:
        List of lowercase phrases to match against notes.
    """
    return get_config().get('conversion.broker_notes_skip_phrases', ['broker'])


def get_pnl_defaults() -> Dict[str, Any]:
    """
    Get PnL algorithm default settings.

    Returns:
        Dictionary containing PnL algorithm configuration.
    """
    return get_config().get_pnl_config()


def get_file_paths() -> Dict[str, str]:
    """
    Get all configured file paths.

    Returns:
        Dictionary mapping path keys to absolute file paths.
    """
    return get_config().get('paths', {})


def get_rins_tradesheet_path() -> str:
    return get_config().get_path('rins_tradesheet')


def get_management_rins_tradesheet_path() -> str:
    return get_config().get_path('management_rins_tradesheet')


def get_rin_prices_path() -> str:
    return get_config().get_path('rin_prices')


def get_trades_path() -> str:
    return get_config().get_path('trades')


def get_additional_trades_path() -> str:
    return get_config().get_path('additional_trades')


def get_fee_schedule_path() -> str:
    return get_config().get_path('fee_schedule')


def get_results_dir() -> str:
    return get_config().get_path('results')


def get_output_template(template_name: str) -> str:
    return get_config().get_template(template_name)


def get_enabled_desks() -> List[str]:
    """
    Get list of enabled desks for PnL calculations.

    Returns:
        List of desk names to include in calculations.
        Empty list means all desks are enabled.
    """
    return get_config().get('desk_filter.enabled_desks', [])


def get_product_blacklist() -> List[str]:
    """
    Get list of blacklisted products to exclude from PnL calculations.

    Returns:
        List of product names to exclude.
    """
    return get_config().get('product_filter.blacklist', [])


_VALID_GRANULARITY_PERIODS: List[str] = ['daily', 'wtd', 'mtd', 'qtd', 'ytd']
_VALID_GRANULARITY_SET = frozenset(_VALID_GRANULARITY_PERIODS)


def get_granularity_config() -> List[str]:
    """
    Get reporting granularity configuration.

    Returns:
        List of enabled time periods (e.g., ['daily', 'wtd', 'mtd', 'qtd', 'ytd']).
        Defaults to all periods if not specified in configuration.
    """
    granularity = get_config().get('granularity', _VALID_GRANULARITY_PERIODS)
    if isinstance(granularity, list):
        return [p for p in granularity if p in _VALID_GRANULARITY_SET]
    return _VALID_GRANULARITY_PERIODS


def get_email_config() -> Dict[str, Any]:
    """
    Get email configuration as a dictionary.

    Credentials are loaded from environment variables for security:
    - EMAIL_USERNAME: SMTP username
    - EMAIL_PASSWORD: SMTP password

    Returns:
        Dictionary containing email configuration including:
        - enabled: Whether email notifications are enabled.
        - smtp_server: SMTP server address.
        - smtp_port: SMTP server port.
        - username: SMTP username (from environment).
        - password: SMTP password (from environment).
        - to_addresses: List of recipient addresses.
    """
    email_config = get_config().get('email', {})

    # Override with environment variables for security
    email_config['username'] = os.getenv('EMAIL_USERNAME', email_config.get('username', ''))
    email_config['password'] = os.getenv('EMAIL_PASSWORD', email_config.get('password', ''))

    return email_config


if __name__ == "__main__":
    # Test configuration loading
    config = get_config()
    print("Configuration loaded successfully!")
    print(f"Min trade date: {get_min_trade_date()}")
    print(f"RINs tradesheet path: {get_rins_tradesheet_path()}")
    print(f"RIN Prices path: {get_rin_prices_path()}")
    print(f"Results directory: {get_results_dir()}")
    print(f"PnL defaults: {get_pnl_defaults()}")
