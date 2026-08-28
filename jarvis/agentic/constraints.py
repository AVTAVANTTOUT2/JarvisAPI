"""Extraction déterministe des contraintes négatives d'une demande.

« Dis-moi si tous les tests passent, mais ne les exécute pas. » créait une
mission et un plan avec ``tests:run`` et ``workspace:write``. Le classifieur ne
lisait que la forme de la demande — « tous les » suffisait à la rendre
multi-étapes — et jamais l'interdiction qui l'accompagnait.

Ce module lit **uniquement** l'interdiction, avant toute élévation de
capacités. Il ne remplace pas le classifieur : il borne ce que le reste du
pipeline pourra en faire. Trois signaux seulement, chacun avec sa preuve
textuelle citée telle quelle :

``no_execution``     rien ne doit être lancé, exécuté ou démarré ;
``no_modification``  rien ne doit être écrit, modifié ou envoyé ;
``answer_only``      « dis-moi seulement » — les deux à la fois.

Les motifs sont fermés et testables hors réseau. Une négation **citée** (entre
guillemets) ou introduite comme exemple (« par exemple », « for example ») est
ignorée : elle décrit une instruction, elle n'en est pas une.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


# Permissions qu'une interdiction retire, quel que soit le profil choisi. La
# liste vise l'effet, pas le nom du profil : ajouter un profil ne rouvre rien.
NO_EXECUTION_BLOCKED_PERMISSIONS: frozenset[str] = frozenset(
    {
        "tests:run",
        "workspace:write",
        "tasks:write",
        "shell:unrestricted",
        "deployment:execute",
        "ocr:run",
        "media:transcode",
        "obs:test",
        "documents:download",
        "desktop:applescript",
    }
)

NO_MODIFICATION_BLOCKED_PERMISSIONS: frozenset[str] = frozenset(
    {
        "workspace:write",
        "tasks:write",
        "drafts:write",
        "communications:send",
        "external:publish",
        "media:publish",
        "stream:public:start",
        "git:push",
        "git:merge",
        "financial:act",
        "shell:unrestricted",
        "deployment:execute",
    }
)


@dataclass(frozen=True)
class RequestConstraints:
    """Interdictions explicites lues dans la demande, avant tout routage."""

    no_execution: bool = False
    no_modification: bool = False
    answer_only: bool = False
    evidence: tuple[str, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        return self.no_execution or self.no_modification

    @property
    def blocked_permissions(self) -> frozenset[str]:
        blocked: frozenset[str] = frozenset()
        if self.no_execution:
            blocked |= NO_EXECUTION_BLOCKED_PERMISSIONS
        if self.no_modification:
            blocked |= NO_MODIFICATION_BLOCKED_PERMISSIONS
        return blocked

    @property
    def tags(self) -> tuple[str, ...]:
        """Étiquettes stables pour le contexte public et le diagnostic."""

        labels = []
        if self.no_execution:
            labels.append("no_execution")
        if self.no_modification:
            labels.append("no_modification")
        if self.answer_only:
            labels.append("answer_only")
        return tuple(labels)

    def public_payload(self) -> dict[str, object]:
        """Vue exposable : les interdictions et leur preuve, rien d'interne."""

        return {
            "no_execution": self.no_execution,
            "no_modification": self.no_modification,
            "answer_only": self.answer_only,
            "evidence": list(self.evidence),
        }


# Une négation citée décrit une instruction sans en être une. On retire les
# segments entre guillemets avant toute recherche de motif.
# Apostrophes ASCII exclues : « don't » / « n'exécute » / « c'est » ne sont
# pas des citations. Seuls les vrais guillemets (y compris ‘…’ appariés) le sont.
_QUOTED = re.compile(
    r"«[^»]*»|\"[^\"]*\"|“[^”]*”|‘[^’]*’|`[^`]*`",
)

# Une négation présentée comme exemple ne s'applique pas non plus. On coupe la
# phrase à l'amorce d'exemple plutôt que d'essayer d'en analyser la portée.
_EXAMPLE_LEAD_IN = re.compile(
    r"\b(?:par exemple|comme quand|quand je (?:dis|dirai|demande)|"
    r"si je (?:dis|demande)|for example|for instance|e\.?g\.?|"
    r"when i say|if i say)\b",
)

# « ne … pas » français et « do not / don't » anglais, appliqués à un verbe
# d'exécution ou de modification. Le verbe est nommé : « ne me dérange pas »
# n'est pas une interdiction d'exécution.
_EXEC_VERBS = (
    r"execute|executes|executez|exec|lance|lances|lancez|lancer|demarre|demarres|"
    r"demarrez|demarrer|run|runs|start|starts|trigger|triggers|launch|launches|"
    r"deploies|deploient|deployez|deployer|deploys|deploying|deploie|deploy|"
    r"merging|merges|mergez|merge|fusionnes|fusionnez|fusionner|fusionne"
)
_WRITE_VERBS = (
    r"modifie|modifies|modifiez|modifier|change|changes|changez|changer|touche|"
    r"touches|touchez|toucher|edite|edites|editez|editer|ecris|ecrit|ecrivez|"
    r"ecrire|corrige|corriges|corrigez|corriger|supprime|supprimes|supprimez|"
    r"commit|commits|push|pushes|envoie|envoies|envoyez|envoyer|publie|publies|"
    r"publiez|modify|change|edit|write|writes|fix|fixes|delete|deletes|send|"
    r"sends|publish|publishes|commit|push|apply|applies"
)
# « ne les/la/leur/lui/l'/le/me … » — l' élidé et lui manquaient.
_CLITICS = r"(?:les?\s+|la\s+|leur\s+|lui\s+|[ml]es\s+|l\s*'?\s*)?"
# Adverbes courants entre le verbe et « pas » : « ne lance surtout pas ».
_ADVERS_BEFORE_NEG = r"(?:(?:\w+)\s+)*"

_NO_EXECUTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"\bne\s+{_CLITICS}(?:{_EXEC_VERBS})\s+{_ADVERS_BEFORE_NEG}(?:pas|rien)\b"
    ),
    re.compile(
        rf"\bn\s*'?\s*(?:{_EXEC_VERBS})\s+{_ADVERS_BEFORE_NEG}(?:pas|rien)\b"
    ),
    re.compile(rf"\bsans\s+(?:les?\s+|la\s+)?(?:{_EXEC_VERBS})\b"),
    re.compile(rf"\b(?:do\s+not|don\s*'?\s*t|never)\s+(?:{_EXEC_VERBS})\b"),
    re.compile(rf"\bwithout\s+(?:{_EXEC_VERBS})(?:ning|ing)?\b"),
    re.compile(r"\bsans\s+(?:rien\s+)?(?:executer|lancer|demarrer|deployer|fusionner)\b"),
    re.compile(r"\bwithout\s+(?:running|executing|starting|launching|deploying|merging)\b"),
    re.compile(r"\bne\s+rien\s+(?:executer|lancer|demarrer|deployer|fusionner)\b"),
)

_NO_MODIFICATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"\bne\s+{_CLITICS}(?:{_WRITE_VERBS})\s+{_ADVERS_BEFORE_NEG}(?:pas|rien)\b"
    ),
    re.compile(
        rf"\bn\s*'?\s*(?:{_WRITE_VERBS})\s+{_ADVERS_BEFORE_NEG}(?:pas|rien)\b"
    ),
    re.compile(rf"\bsans\s+(?:les?\s+|la\s+|rien\s+)?(?:{_WRITE_VERBS})r?\b"),
    re.compile(rf"\b(?:do\s+not|don\s*'?\s*t|never)\s+(?:{_WRITE_VERBS})\b"),
    re.compile(r"\bsans\s+(?:rien\s+)?(?:modifier|changer|toucher|ecrire|envoyer)\b"),
    re.compile(r"\bne\s+rien\s+(?:modifier|changer|toucher|ecrire|envoyer)\b"),
    re.compile(r"\bwithout\s+(?:modifying|changing|editing|writing|touching)\b"),
    re.compile(r"\blecture\s+seule\b"),
    re.compile(r"\bread[\s-]?only\b"),
    re.compile(r"\ben\s+lecture\s+seule\b"),
)

_ANSWER_ONLY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:dis|dites|donne|donnez)[\s-]*(?:moi|nous)\s+(?:juste|seulement|simplement)\b"
    ),
    re.compile(
        r"\b(?:juste|seulement|simplement)\s+(?:dis|dites|donne|donnez)[\s-]*(?:moi|nous)\b"
    ),
    re.compile(r"\b(?:just|only|simply)\s+tell\s+me\b"),
    re.compile(r"\btell\s+me\s+(?:only|just)\b"),
    re.compile(r"\bcontente[\s-]toi\s+de\s+(?:me\s+)?(?:dire|repondre)\b"),
    re.compile(r"\breponds?\s+(?:moi\s+)?(?:juste|seulement)\b"),
)


def _fold(text: str) -> str:
    """Minuscules sans accents, espaces normalisés — comparaison stable."""

    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    # Les apostrophes typographiques et les tirets longs deviennent leurs
    # équivalents ASCII pour que « n'exécute » et « n’exécute » soient un seul
    # motif.
    stripped = stripped.replace("’", "'").replace("‘", "'")
    return " ".join(stripped.lower().split())


def _matchable(text: str) -> str:
    """Retire les citations et coupe aux amorces d'exemple."""

    without_quotes = _QUOTED.sub(" ", text or "")
    folded = _fold(without_quotes)
    example = _EXAMPLE_LEAD_IN.search(folded)
    if example is not None:
        folded = folded[: example.start()]
    return folded


def _first_match(patterns: tuple[re.Pattern[str], ...], text: str) -> str | None:
    for pattern in patterns:
        found = pattern.search(text)
        if found is not None:
            return found.group(0).strip()
    return None


def extract_request_constraints(request: str) -> RequestConstraints:
    """Lit les interdictions explicites d'une demande, sans modèle ni réseau."""

    text = _matchable(request)
    if not text:
        return RequestConstraints()

    evidence: list[str] = []
    answer_only_hit = _first_match(_ANSWER_ONLY_PATTERNS, text)
    no_exec_hit = _first_match(_NO_EXECUTION_PATTERNS, text)
    no_write_hit = _first_match(_NO_MODIFICATION_PATTERNS, text)

    for hit in (answer_only_hit, no_exec_hit, no_write_hit):
        if hit and hit not in evidence:
            evidence.append(hit)

    answer_only = answer_only_hit is not None
    return RequestConstraints(
        # « dis-moi seulement » interdit les deux : c'est une demande de
        # réponse, pas de travail.
        no_execution=answer_only or no_exec_hit is not None,
        no_modification=answer_only or no_write_hit is not None,
        answer_only=answer_only,
        evidence=tuple(evidence),
    )


__all__ = [
    "NO_EXECUTION_BLOCKED_PERMISSIONS",
    "NO_MODIFICATION_BLOCKED_PERMISSIONS",
    "RequestConstraints",
    "extract_request_constraints",
]
