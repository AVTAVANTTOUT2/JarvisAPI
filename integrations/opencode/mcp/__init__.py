"""Bridge MCP local et confiné du runtime OpenCode.

Le coeur JARVIS ne dépend jamais de ce package. Le processus provider le
découvre depuis son manifest et lance le serveur stdio pour un run précis.
"""

from .capabilities import CapabilityEnvelope, CapabilityError

__all__ = ["CapabilityEnvelope", "CapabilityError"]
