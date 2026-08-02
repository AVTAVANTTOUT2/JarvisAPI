"""Sélecteurs Uber Eats externalisés, validés et résolus à l'exécution.

Uber ne publie aucun contrat de stabilité sur son DOM. Écrire les sélecteurs
en dur dans le code condamnerait à modifier du Python à chaque refonte de leur
frontend. Ils vivent donc dans un fichier JSON versionné où chaque rôle
logique (« la barre de recherche », « le bouton de commande ») déclare une
liste ordonnée de stratégies candidates, essayées jusqu'à la première qui
apparaît réellement à l'écran.

Le fichier porte un drapeau ``verified``. Tant qu'il est faux, les sélecteurs
livrés par défaut sont des hypothèses non confirmées et l'intégration refuse
de sortir du mode simulation : c'est le garde-fou qui empêche de cliquer au
hasard sur une page de paiement.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from integrations.playwright_runtime import playwright_errors

if TYPE_CHECKING:  # pragma: no cover - typage seulement
    from playwright.async_api import Locator, Page

logger = logging.getLogger("jarvis.uber_eats")

SUPPORTED_SCHEMA_VERSION = 1

#: Stratégies acceptées, alignées sur les API de localisation de Playwright.
STRATEGY_KINDS: frozenset[str] = frozenset(
    {"test_id", "role", "placeholder", "label", "alt_text", "title", "text", "css"}
)

#: Rôles sans lesquels aucun parcours de commande n'est possible.
REQUIRED_ROLES: tuple[str, ...] = (
    "login_marker",
    "session_marker",
    "search_input",
    "store_card",
    "menu_item",
    "add_to_cart_button",
    "cart_button",
    "cart_total",
    "checkout_button",
    "place_order_button",
    "order_confirmation_marker",
)

#: Rôles utiles mais dont l'absence dégrade sans casser le parcours.
#: Les rôles de découverte (relevé de menus, suivi de livraison) sont
#: optionnels par construction : ils servent des fonctions de confort et ne
#: doivent jamais empêcher une commande de passer si Uber change ces écrans.
OPTIONAL_ROLES: tuple[str, ...] = (
    "cookie_accept_button",
    "address_prompt",
    "quantity_increase_button",
    "feed_store_card",
    "feed_store_title",
    "menu_section",
    "menu_section_title",
    "menu_entry",
    "menu_entry_title",
    "menu_entry_price",
    "order_status_text",
    "order_eta_text",
)

KNOWN_ROLES: frozenset[str] = frozenset(REQUIRED_ROLES + OPTIONAL_ROLES)

#: Substitutions autorisées par rôle, pour rejeter les gabarits mal orthographiés.
ROLE_PLACEHOLDERS: Mapping[str, frozenset[str]] = {
    "menu_item": frozenset({"item"}),
    "store_card": frozenset({"restaurant"}),
}

DEFAULT_PLACEHOLDERS: frozenset[str] = frozenset()

_MAX_VALUE_CHARS = 400


class SelectorConfigError(RuntimeError):
    """Le fichier de sélecteurs est absent, illisible ou invalide."""


class SelectorResolutionError(RuntimeError):
    """Aucune stratégie candidate n'a permis de trouver l'élément visé."""


@dataclass(frozen=True, slots=True)
class LocatorStrategy:
    """Une façon de désigner un élément de page.

    Attributes:
        kind: Stratégie Playwright utilisée (voir ``STRATEGY_KINDS``).
        value: Valeur textuelle associée (nom accessible, texte, sélecteur CSS…).
        aria_role: Rôle ARIA, obligatoire quand ``kind == "role"``.
        exact: Correspondance stricte du texte plutôt que partielle.
        index: Rang de l'élément lorsque plusieurs correspondent.
    """

    kind: str
    value: str = ""
    aria_role: str = ""
    exact: bool = False
    index: int = 0

    def describe(self) -> str:
        """Description compacte, destinée aux messages d'erreur."""
        parts = [self.kind]
        if self.aria_role:
            parts.append(f"role={self.aria_role}")
        if self.value:
            parts.append(f"value={self.value!r}")
        if self.exact:
            parts.append("exact")
        if self.index:
            parts.append(f"nth={self.index}")
        return " ".join(parts)

    def render(self, substitutions: Mapping[str, str] | None = None) -> LocatorStrategy:
        """Remplace les gabarits ``{clé}`` par les valeurs fournies."""
        if not substitutions or "{" not in self.value:
            return self
        rendered = self.value
        for key, value in substitutions.items():
            rendered = rendered.replace("{" + key + "}", str(value))
        return replace(self, value=rendered)


@dataclass(frozen=True, slots=True)
class SelectorMap:
    """Carte des sélecteurs chargée depuis le disque."""

    version: int
    verified: bool
    captured_at: str | None
    source: Path
    strategies: Mapping[str, tuple[LocatorStrategy, ...]]

    def has(self, role: str) -> bool:
        """Indique si le rôle dispose d'au moins une stratégie."""
        return bool(self.strategies.get(role))

    def candidates(self, role: str) -> tuple[LocatorStrategy, ...]:
        """Stratégies déclarées pour un rôle, dans l'ordre d'essai."""
        found = self.strategies.get(role)
        if not found:
            raise SelectorConfigError(
                f"Rôle de sélecteur absent : {role!r} dans {self.source} "
                f"(rôles déclarés : {sorted(self.strategies)})"
            )
        return found


def _parse_strategy(role: str, index: int, raw: Any, source: Path) -> LocatorStrategy:
    """Valide une entrée candidate et la convertit en ``LocatorStrategy``."""
    where = f"{source} → locators.{role}[{index}]"
    if not isinstance(raw, Mapping):
        raise SelectorConfigError(f"{where} : objet JSON attendu, reçu {type(raw).__name__}")

    kind = str(raw.get("strategy", "")).strip()
    if kind not in STRATEGY_KINDS:
        raise SelectorConfigError(
            f"{where} : stratégie inconnue {kind!r} (attendu parmi {sorted(STRATEGY_KINDS)})"
        )

    value = raw.get("value", "")
    if not isinstance(value, str):
        raise SelectorConfigError(f"{where} : 'value' doit être une chaîne")
    value = value.strip()
    if len(value) > _MAX_VALUE_CHARS:
        raise SelectorConfigError(
            f"{where} : 'value' dépasse {_MAX_VALUE_CHARS} caractères ({len(value)})"
        )

    aria_role = raw.get("role", "")
    if not isinstance(aria_role, str):
        raise SelectorConfigError(f"{where} : 'role' doit être une chaîne")
    aria_role = aria_role.strip()

    if kind == "role" and not aria_role:
        raise SelectorConfigError(f"{where} : la stratégie 'role' exige un champ 'role'")
    if kind != "role" and not value:
        raise SelectorConfigError(f"{where} : la stratégie {kind!r} exige un champ 'value'")

    exact = raw.get("exact", False)
    if not isinstance(exact, bool):
        raise SelectorConfigError(f"{where} : 'exact' doit être un booléen")

    nth = raw.get("index", 0)
    if not isinstance(nth, int) or isinstance(nth, bool) or nth < 0:
        raise SelectorConfigError(f"{where} : 'index' doit être un entier positif ou nul")

    _validate_placeholders(role, value, where)
    return LocatorStrategy(
        kind=kind, value=value, aria_role=aria_role, exact=exact, index=nth
    )


def _validate_placeholders(role: str, value: str, where: str) -> None:
    """Rejette les gabarits non prévus pour ce rôle (typo silencieuse sinon)."""
    allowed = ROLE_PLACEHOLDERS.get(role, DEFAULT_PLACEHOLDERS)
    depth = 0
    current: list[str] = []
    for char in value:
        if char == "{":
            depth += 1
            current = []
            continue
        if char == "}" and depth:
            depth -= 1
            token = "".join(current)
            if token not in allowed:
                raise SelectorConfigError(
                    f"{where} : gabarit {{{token}}} non autorisé pour le rôle {role!r} "
                    f"(autorisés : {sorted(allowed) or 'aucun'})"
                )
            continue
        if depth:
            current.append(char)
    if depth:
        raise SelectorConfigError(f"{where} : accolade ouvrante non fermée dans 'value'")


def parse_selector_document(document: Any, source: Path) -> SelectorMap:
    """Valide un document JSON déjà chargé et retourne la carte correspondante."""
    if not isinstance(document, Mapping):
        raise SelectorConfigError(f"{source} : objet JSON attendu à la racine")

    version = document.get("version")
    if version != SUPPORTED_SCHEMA_VERSION:
        raise SelectorConfigError(
            f"{source} : version de schéma {version!r} non supportée "
            f"(attendu {SUPPORTED_SCHEMA_VERSION})"
        )

    verified = document.get("verified", False)
    if not isinstance(verified, bool):
        raise SelectorConfigError(f"{source} : 'verified' doit être un booléen")

    captured_at = document.get("captured_at")
    if captured_at is not None and not isinstance(captured_at, str):
        raise SelectorConfigError(f"{source} : 'captured_at' doit être une chaîne ou null")

    locators = document.get("locators")
    if not isinstance(locators, Mapping):
        raise SelectorConfigError(f"{source} : 'locators' doit être un objet JSON")

    unknown = sorted(set(map(str, locators)) - KNOWN_ROLES)
    if unknown:
        raise SelectorConfigError(
            f"{source} : rôles inconnus {unknown} (connus : {sorted(KNOWN_ROLES)})"
        )

    strategies: dict[str, tuple[LocatorStrategy, ...]] = {}
    for role, raw_candidates in locators.items():
        if not isinstance(raw_candidates, Sequence) or isinstance(raw_candidates, (str, bytes)):
            raise SelectorConfigError(f"{source} → locators.{role} : liste attendue")
        if not raw_candidates:
            raise SelectorConfigError(
                f"{source} → locators.{role} : au moins une stratégie est requise"
            )
        strategies[str(role)] = tuple(
            _parse_strategy(str(role), index, raw, source)
            for index, raw in enumerate(raw_candidates)
        )

    missing = [role for role in REQUIRED_ROLES if role not in strategies]
    if missing:
        raise SelectorConfigError(
            f"{source} : rôles obligatoires manquants {missing}. "
            "Relancer scripts/uber_eats_capture_session.py pour les capturer."
        )

    return SelectorMap(
        version=int(version),
        verified=verified,
        captured_at=captured_at,
        source=source,
        strategies=strategies,
    )


_cache_lock = threading.Lock()
_cache: dict[str, tuple[tuple[int, int], SelectorMap]] = {}


def load_selector_map(path: str | Path) -> SelectorMap:
    """Charge et valide la carte des sélecteurs, avec cache invalidé par mtime.

    Le cache permet de recharger un fichier fraîchement recapturé sans
    redémarrer JARVIS, tout en évitant de relire le disque à chaque commande.

    Raises:
        SelectorConfigError: fichier absent, JSON invalide ou schéma non conforme.
    """
    source = Path(path)
    if source.is_symlink():
        raise SelectorConfigError(
            f"Fichier de sélecteurs refusé (lien symbolique) : {source}"
        )
    try:
        stat = source.stat()
    except OSError as exc:
        raise SelectorConfigError(
            f"Fichier de sélecteurs illisible : {source} ({exc.strerror or exc})"
        ) from exc
    if not source.is_file():
        raise SelectorConfigError(f"Fichier de sélecteurs introuvable : {source}")

    signature = (stat.st_mtime_ns, stat.st_size)
    key = str(source.resolve())
    with _cache_lock:
        cached = _cache.get(key)
        if cached and cached[0] == signature:
            return cached[1]

    try:
        raw_text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise SelectorConfigError(
            f"Lecture impossible du fichier de sélecteurs {source} : {exc}"
        ) from exc
    try:
        document = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise SelectorConfigError(
            f"JSON invalide dans {source} ligne {exc.lineno} colonne {exc.colno} : {exc.msg}"
        ) from exc

    selector_map = parse_selector_document(document, source)
    with _cache_lock:
        _cache[key] = (signature, selector_map)
    return selector_map


def clear_selector_cache() -> None:
    """Vide le cache de sélecteurs. Utilisé par les tests et après capture."""
    with _cache_lock:
        _cache.clear()


def build_locator(page: "Page | Locator", strategy: LocatorStrategy) -> "Locator":
    """Construit un locator Playwright à partir d'une stratégie déjà rendue.

    ``page`` accepte aussi bien la page qu'un locator parent : les méthodes de
    recherche de Playwright ont la même signature sur les deux, ce qui permet
    de descendre dans une carte de menu sans dupliquer la logique.
    """
    if strategy.kind == "test_id":
        locator = page.get_by_test_id(strategy.value)
    elif strategy.kind == "role":
        if strategy.value:
            locator = page.get_by_role(
                strategy.aria_role, name=strategy.value, exact=strategy.exact
            )
        else:
            locator = page.get_by_role(strategy.aria_role)
    elif strategy.kind == "placeholder":
        locator = page.get_by_placeholder(strategy.value, exact=strategy.exact)
    elif strategy.kind == "label":
        locator = page.get_by_label(strategy.value, exact=strategy.exact)
    elif strategy.kind == "alt_text":
        locator = page.get_by_alt_text(strategy.value, exact=strategy.exact)
    elif strategy.kind == "title":
        locator = page.get_by_title(strategy.value, exact=strategy.exact)
    elif strategy.kind == "text":
        locator = page.get_by_text(strategy.value, exact=strategy.exact)
    elif strategy.kind == "css":
        locator = page.locator(strategy.value)
    else:  # pragma: no cover - verrouillé par la validation de chargement
        raise SelectorConfigError(f"Stratégie non implémentée : {strategy.kind!r}")
    return locator.nth(strategy.index)


async def resolve_locator(
    page: "Page",
    selector_map: SelectorMap,
    role: str,
    *,
    timeout_ms: int,
    substitutions: Mapping[str, str] | None = None,
    state: str = "visible",
) -> "Locator":
    """Retourne le premier locator candidat qui atteint l'état demandé.

    Raises:
        SelectorResolutionError: aucune stratégie n'a abouti ; le message liste
            chaque tentative et son échec, pour permettre une recapture ciblée.
    """
    errors = playwright_errors()
    attempts: list[str] = []
    for strategy in selector_map.candidates(role):
        rendered = strategy.render(substitutions)
        locator = build_locator(page, rendered)
        try:
            await locator.wait_for(state=state, timeout=timeout_ms)
        except errors as exc:
            attempts.append(f"{rendered.describe()} → {type(exc).__name__}")
            continue
        logger.debug("[uber_eats] rôle %s résolu par %s", role, rendered.describe())
        return locator
    raise SelectorResolutionError(
        f"Rôle {role!r} introuvable sur {page.url} après {len(attempts)} tentative(s) "
        f"depuis {selector_map.source} : {'; '.join(attempts) or 'aucune stratégie'}. "
        "Recapturer les sélecteurs via scripts/uber_eats_capture_session.py."
    )


async def resolve_all(
    scope: "Page | Locator",
    selector_map: SelectorMap,
    role: str,
    *,
    limit: int,
) -> list["Locator"]:
    """Retourne tous les éléments du premier candidat qui en trouve au moins un.

    Destiné aux relevés en lecture seule (cartes du fil, lignes de menu), où
    l'absence d'élément est une information et non une erreur : la liste vide
    est un retour normal.
    """
    errors = playwright_errors()
    if not selector_map.has(role):
        return []
    for strategy in selector_map.candidates(role):
        locator = build_locator_collection(scope, strategy)
        try:
            found = await locator.all()
        except errors as exc:
            logger.debug("[uber_eats] rôle %s non listable (%s)", role, exc)
            continue
        if found:
            return found[: max(0, limit)]
    return []


def build_locator_collection(
    scope: "Page | Locator", strategy: LocatorStrategy
) -> "Locator":
    """Construit un locator non restreint à un rang, pour énumérer les éléments."""
    return build_locator(scope, replace(strategy, index=0) if strategy.index else strategy)


async def read_text(
    scope: "Page | Locator",
    selector_map: SelectorMap,
    role: str,
    *,
    timeout_ms: int,
) -> str:
    """Lit le texte du premier candidat résolu, ou une chaîne vide.

    Ne lève jamais : un champ de confort manquant ne doit pas interrompre un
    relevé qui a déjà collecté des informations utiles.
    """
    errors = playwright_errors()
    if not selector_map.has(role):
        return ""
    for strategy in selector_map.candidates(role):
        locator = build_locator(scope, strategy)
        try:
            return (await locator.inner_text(timeout=timeout_ms)).strip()
        except errors as exc:
            logger.debug("[uber_eats] texte du rôle %s illisible : %s", role, exc)
            continue
    return ""


async def role_is_visible(
    page: "Page",
    selector_map: SelectorMap,
    role: str,
    *,
    timeout_ms: int,
    substitutions: Mapping[str, str] | None = None,
) -> bool:
    """Teste la présence visible d'un rôle sans lever si rien n'apparaît."""
    if not selector_map.has(role):
        return False
    try:
        await resolve_locator(
            page,
            selector_map,
            role,
            timeout_ms=timeout_ms,
            substitutions=substitutions,
        )
    except SelectorResolutionError:
        return False
    return True
