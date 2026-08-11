"""SDK Python officiel pour JARVIS."""

from .client import JarvisClient
from .errors import (
    JarvisApiError,
    JarvisAuthenticationError,
    JarvisConfigurationError,
    JarvisError,
    JarvisResponseTooLarge,
    JarvisTransportError,
)
from .models import JarvisResponse, Operation
from .operations import CONTRACT_VERSION, OPERATIONS

__version__ = CONTRACT_VERSION

__all__ = [
    "CONTRACT_VERSION",
    "OPERATIONS",
    "JarvisApiError",
    "JarvisAuthenticationError",
    "JarvisClient",
    "JarvisConfigurationError",
    "JarvisError",
    "JarvisResponse",
    "JarvisResponseTooLarge",
    "JarvisTransportError",
    "Operation",
    "__version__",
]
