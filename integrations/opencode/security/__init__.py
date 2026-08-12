"""Primitives de confinement du provider OpenCode."""

from .environment import build_child_environment
from .paths import PathSecurityError, ensure_within, validate_loopback_url
from .prompt_injection import UntrustedContent, bound_untrusted_content
from .redaction import REDACTED, redact_mapping, redact_text

__all__ = [
    "PathSecurityError",
    "REDACTED",
    "UntrustedContent",
    "bound_untrusted_content",
    "build_child_environment",
    "ensure_within",
    "redact_mapping",
    "redact_text",
    "validate_loopback_url",
]
