"""Application-wide error types."""


class ScannerError(Exception):
    """Base error for the scanner."""


class ConfigError(ScannerError):
    pass


class AuthError(ScannerError):
    """Kite session missing, expired or invalid."""


class EntitlementError(AuthError):
    """Account lacks historical-market-data API access."""


class DataError(ScannerError):
    """Data download / validation failure."""
