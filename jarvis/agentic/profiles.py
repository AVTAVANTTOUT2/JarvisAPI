"""Profils de capacités minimaux décidés par le routeur JARVIS.

Les profils décrivent une politique JARVIS, pas une configuration fournisseur.
Un runtime ne reçoit que les permissions persistées pour le run, lesquelles
doivent rester incluses dans le profil choisi ici.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
import unicodedata

from .constraints import RequestConstraints, extract_request_constraints
from .models import AgenticRequestCategory


CAPABILITY_PROFILE_CONTEXT_KEY = "capability_profile_id"


@dataclass(frozen=True)
class CapabilityProfile:
    """Borne immuable des permissions accordables à un run agentique."""

    profile_id: str
    permissions: tuple[str, ...]
    default_permissions: tuple[str, ...]
    denied_permissions: tuple[str, ...] = ()
    approval_permissions: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.profile_id or self.profile_id != self.profile_id.strip():
            raise ValueError("identifiant de profil de capacités invalide")
        permissions = tuple(dict.fromkeys(self.permissions))
        defaults = tuple(dict.fromkeys(self.default_permissions))
        denied = tuple(dict.fromkeys(self.denied_permissions))
        approvals = tuple(dict.fromkeys(self.approval_permissions))
        if not set(defaults).issubset(permissions):
            raise ValueError("les permissions par défaut doivent être autorisées")
        if not set(approvals).issubset(permissions):
            raise ValueError("une permission à approuver doit être autorisée")
        if set(permissions).intersection(denied):
            raise ValueError("une permission ne peut être autorisée et interdite")
        object.__setattr__(self, "permissions", permissions)
        object.__setattr__(self, "default_permissions", defaults)
        object.__setattr__(self, "denied_permissions", denied)
        object.__setattr__(self, "approval_permissions", approvals)
        object.__setattr__(self, "constraints", tuple(dict.fromkeys(self.constraints)))

    def refused_permissions(self, requested: tuple[str, ...]) -> tuple[str, ...]:
        """Retourne les permissions hors profil, sans les accorder implicitement."""

        allowed = frozenset(self.permissions)
        return tuple(sorted(set(requested) - allowed))


_PROFILES = (
    CapabilityProfile(
        profile_id="readonly-research",
        permissions=(
            "communications:read",
            "calendar:read",
            "conversations:read",
            "memory:read",
            "contacts:read",
            "media:read",
            "documents:read",
            "tasks:read",
            "project_state:read",
            "research:search",
            "workspace:read",
        ),
        default_permissions=(
            "communications:read",
            "calendar:read",
            "conversations:read",
            "memory:read",
            "contacts:read",
            "media:read",
            "documents:read",
            "tasks:read",
            "project_state:read",
            "research:search",
            "workspace:read",
        ),
        denied_permissions=(
            "workspace:write",
            "shell:unrestricted",
            "communications:send",
            "external:publish",
            "privilege:elevate",
        ),
        constraints=("read_only", "no_free_shell", "no_external_send"),
    ),
    CapabilityProfile(
        profile_id="coding",
        permissions=(
            "workspace:read",
            "workspace:write",
            "tests:run",
            "lsp:use",
            "documentation:read",
        ),
        default_permissions=("workspace:read", "workspace:write", "tests:run"),
        denied_permissions=(
            "secrets:read",
            "shell:unrestricted",
            "git:push",
            "git:merge",
            "deployment:execute",
        ),
        approval_permissions=("workspace:write",),
        constraints=("isolated_worktree", "no_push_merge_or_deploy"),
    ),
    CapabilityProfile(
        profile_id="communication",
        permissions=(
            "communications:read",
            "conversations:read",
            "drafts:write",
            "contacts:read",
            "communications:send",
        ),
        default_permissions=(
            "communications:read",
            "conversations:read",
            "drafts:write",
            "contacts:read",
            "communications:send",
        ),
        denied_permissions=("shell:unrestricted",),
        approval_permissions=("communications:send",),
        constraints=("authorized_conversations_only", "send_subject_to_policy"),
    ),
    CapabilityProfile(
        profile_id="browser",
        permissions=("browser:control", "browser:download", "research:search"),
        default_permissions=(
            "browser:control",
            "browser:download",
            "research:search",
        ),
        denied_permissions=("filesystem:arbitrary", "financial:act"),
        constraints=(
            "allowed_domains_only",
            "confined_downloads",
            "captcha_and_2fa_are_blockers",
            "no_financial_action",
        ),
    ),
    CapabilityProfile(
        profile_id="invoice",
        permissions=(
            "browser:supplier",
            "documents:download",
            "ocr:run",
            "documents:classify",
            "duplicates:detect",
        ),
        default_permissions=(
            "browser:supplier",
            "documents:download",
            "ocr:run",
            "documents:classify",
            "duplicates:detect",
        ),
        denied_permissions=("financial:act",),
        constraints=("supplier_domains_only", "no_financial_action"),
    ),
    CapabilityProfile(
        profile_id="obs",
        permissions=(
            "obs:websocket",
            "obs:scenes",
            "obs:sources",
            "obs:audio",
            "obs:test",
            "stream:public:start",
        ),
        default_permissions=(
            "obs:websocket",
            "obs:scenes",
            "obs:sources",
            "obs:audio",
            "obs:test",
            "stream:public:start",
        ),
        approval_permissions=("stream:public:start",),
        constraints=("local_test_first", "public_live_requires_approval"),
    ),
    CapabilityProfile(
        profile_id="media",
        permissions=(
            "media:read",
            "media:probe",
            "media:transcode",
            "media:transcribe",
            "media:preview",
            "media:publish",
        ),
        default_permissions=(
            "media:read",
            "media:probe",
            "media:transcode",
            "media:transcribe",
            "media:preview",
            "media:publish",
        ),
        approval_permissions=("media:publish",),
        constraints=("authorized_media_only", "publication_requires_approval"),
    ),
    CapabilityProfile(
        profile_id="desktop",
        permissions=(
            "desktop:macos",
            "desktop:applescript",
            "desktop:accessibility",
        ),
        default_permissions=(
            "desktop:macos",
            "desktop:applescript",
            "desktop:accessibility",
        ),
        denied_permissions=("privilege:elevate",),
        constraints=("explicit_macos_capabilities_only", "no_privilege_escalation"),
    ),
)

CAPABILITY_PROFILES: Mapping[str, CapabilityProfile] = MappingProxyType(
    {profile.profile_id: profile for profile in _PROFILES}
)

_CATEGORY_DEFAULTS: Mapping[AgenticRequestCategory, str] = MappingProxyType(
    {
        AgenticRequestCategory.DIRECT_ACTION: "readonly-research",
        AgenticRequestCategory.AGENTIC_READONLY: "readonly-research",
        AgenticRequestCategory.AGENTIC_REVERSIBLE: "coding",
    }
)

_CATEGORY_COMPATIBLE: Mapping[AgenticRequestCategory, frozenset[str]] = (
    MappingProxyType(
        {
            AgenticRequestCategory.DIRECT_ACTION: frozenset({"readonly-research"}),
            AgenticRequestCategory.AGENTIC_READONLY: frozenset({"readonly-research"}),
            AgenticRequestCategory.AGENTIC_REVERSIBLE: frozenset(
                {"readonly-research", "coding"}
            ),
            AgenticRequestCategory.WORKFLOW: frozenset(CAPABILITY_PROFILES),
            AgenticRequestCategory.AGENTIC_EXTERNAL_EFFECT: frozenset(
                {
                    "readonly-research",
                    "communication",
                    "browser",
                    "invoice",
                    "obs",
                    "media",
                    "desktop",
                }
            ),
            AgenticRequestCategory.AGENTIC_HIGH_RISK: frozenset(
                {
                    "readonly-research",
                    "communication",
                    "browser",
                    "invoice",
                    "obs",
                    "media",
                    "desktop",
                }
            ),
        }
    )
)

_INTENT_HINTS: tuple[tuple[str, frozenset[str]], ...] = (
    ("invoice", frozenset({"facture", "invoice", "ocr", "fournisseur"})),
    ("obs", frozenset({"obs", "scene", "source obs", "live public"})),
    (
        "media",
        frozenset(
            {
                "ffmpeg",
                "ffprobe",
                "media",
                "video",
                "transcription",
                "transcrire",
                "montage",
                "preview",
                "apple music",
                "musique",
                "music",
                "playlist",
                "morceau",
                "chanson",
            }
        ),
    ),
    (
        "communication",
        frozenset(
            {
                "message",
                "email",
                "courriel",
                "contact",
                "brouillon",
                "conversation",
            }
        ),
    ),
    (
        "browser",
        frozenset(
            {
                "navigateur",
                "browser",
                "site web",
                "page web",
                "captcha",
                "2fa",
                "hotel",
                "airbnb",
                "hostel",
                "booking",
                "voyage",
                "a trip",
                "the trip",
                "flight",
                "vol pour",
                "restaurant",
                "resto",
                "billet",
                "ticket",
                "concert",
            }
        ),
    ),
    (
        "desktop",
        frozenset({"macos", "applescript", "accessibilite", "desktop", "app mac"}),
    ),
    (
        "coding",
        frozenset(
            {
                "code",
                "repo",
                "repository",
                "worktree",
                "test",
                "lsp",
                "refactoriser",
                "implementer",
                "corriger",
                "html",
                "css",
                "javascript",
                "todolist",
                "migration",
            }
        ),
    ),
)


def constrain_capability_profile(
    profile: CapabilityProfile,
    constraints: RequestConstraints,
) -> CapabilityProfile:
    """Retire du profil les permissions que la demande interdit explicitement.

    La permission est retirée de ``permissions``, pas seulement des valeurs par
    défaut : ``refused_permissions()`` refuse ensuite toute liste persistée qui
    la contiendrait encore. Une capacité interdite ne peut donc pas
    réapparaître plus loin dans le pipeline via des métadonnées de routage.
    """

    blocked = constraints.blocked_permissions
    if not blocked or not blocked.intersection(profile.permissions):
        return profile
    return CapabilityProfile(
        profile_id=profile.profile_id,
        permissions=tuple(p for p in profile.permissions if p not in blocked),
        default_permissions=tuple(
            p for p in profile.default_permissions if p not in blocked
        ),
        denied_permissions=tuple(
            dict.fromkeys(profile.denied_permissions + tuple(sorted(blocked)))
        ),
        approval_permissions=tuple(
            p for p in profile.approval_permissions if p not in blocked
        ),
        constraints=profile.constraints + constraints.tags,
    )


def constrain_capability_profile_for_request(
    profile: CapabilityProfile, request: str
) -> CapabilityProfile:
    """Ré-applique les interdictions de la demande à un profil déjà résolu."""

    return constrain_capability_profile(profile, extract_request_constraints(request))


def get_capability_profile(profile_id: str) -> CapabilityProfile:
    """Résout un identifiant exact ou refuse un profil inconnu."""

    try:
        return CAPABILITY_PROFILES[profile_id]
    except (KeyError, TypeError) as exc:
        raise ValueError("profil de capacités JARVIS inconnu") from exc


def capability_profile_id_from_context(context: Mapping[str, object]) -> str | None:
    """Lit le marqueur JARVIS persisté, sans accepter de coercition permissive."""

    value = context.get(CAPABILITY_PROFILE_CONTEXT_KEY)
    if value is None:
        return None
    if not isinstance(value, str) or value not in CAPABILITY_PROFILES:
        raise ValueError("profil de capacités persisté invalide")
    return value


def _normalized(text: str) -> str:
    value = unicodedata.normalize("NFKD", (text or "").lower())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(value.split())


def select_capability_profile(
    request: str,
    category: AgenticRequestCategory | str,
    *,
    default_profile_id: str = "readonly-research",
    route_overrides: Mapping[str, str] | None = None,
) -> CapabilityProfile:
    """Sélectionne la borne minimale avant tout appel au runtime.

    Une surcharge de configuration ne peut pas contourner la compatibilité de
    catégorie. En particulier, une demande read-only ne peut jamais obtenir le
    profil ``coding`` par simple prompt ou configuration.
    """

    selected_category = AgenticRequestCategory(category)
    compatible = _CATEGORY_COMPATIBLE[selected_category]
    # Les interdictions de la demande bornent le profil quelle que soit la
    # branche empruntée — surcharge de configuration comprise. Une surcharge
    # ne peut pas rendre à un run une capacité que l'utilisateur a interdite.
    constraints = extract_request_constraints(request)
    configured = (route_overrides or {}).get(selected_category.value)
    if configured is not None:
        configured_profile = get_capability_profile(configured)
        if configured_profile.profile_id not in compatible:
            raise ValueError("surcharge de profil incompatible avec la catégorie")
        return constrain_capability_profile(configured_profile, constraints)

    fixed_default = _CATEGORY_DEFAULTS.get(selected_category)
    if fixed_default is not None:
        return constrain_capability_profile(
            CAPABILITY_PROFILES[fixed_default], constraints
        )

    normalized_request = _normalized(request)
    for profile_id, hints in _INTENT_HINTS:
        if profile_id in compatible and any(
            hint in normalized_request for hint in hints
        ):
            return constrain_capability_profile(
                CAPABILITY_PROFILES[profile_id], constraints
            )

    fallback = get_capability_profile(default_profile_id)
    if fallback.profile_id not in compatible:
        fallback = CAPABILITY_PROFILES["readonly-research"]
    return constrain_capability_profile(fallback, constraints)


__all__ = [
    "CAPABILITY_PROFILE_CONTEXT_KEY",
    "CAPABILITY_PROFILES",
    "CapabilityProfile",
    "capability_profile_id_from_context",
    "constrain_capability_profile",
    "constrain_capability_profile_for_request",
    "get_capability_profile",
    "select_capability_profile",
]
