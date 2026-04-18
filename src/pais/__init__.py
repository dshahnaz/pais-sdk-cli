"""PAIS SDK — contract-first client for VMware Private AI Service."""

from pais.client import PaisClient
from pais.config import Settings
from pais.errors import (
    IndexDeleteUnsupported,
    PaisAuthError,
    PaisError,
    PaisNotFoundError,
    PaisRateLimitError,
    PaisServerError,
    PaisTimeoutError,
    PaisValidationError,
)

__all__ = [
    "IndexDeleteUnsupported",
    "PaisAuthError",
    "PaisClient",
    "PaisError",
    "PaisNotFoundError",
    "PaisRateLimitError",
    "PaisServerError",
    "PaisTimeoutError",
    "PaisValidationError",
    "Settings",
]

__version__ = "0.8.2"
