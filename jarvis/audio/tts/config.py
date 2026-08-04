"""Configuration de la synthèse vocale locale — un seul endroit.

Avant ce module, le moteur, la voix et les seuils vivaient dans une dizaine de
variables préfixées par le nom du fournisseur (``KOKORO_VOICE``,
``TTS_SPEAKER``, ``MACOS_TTS_VOICE``…). Changer de moteur obligeait à changer
de variables, donc à toucher tous les fichiers qui les lisaient.

Ici, un seul jeu de réglages décrit ce dont le pipeline a besoin, quel que soit
le backend. Ce que la configuration ne contient pas est aussi important :
aucune clé d'API, aucune URL, aucun hôte. Un fournisseur qui en réclamerait ne
pourrait pas être configuré.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# ── Défauts versionnés ──────────────────────────────────────────────────────
# Une installation neuve doit obtenir une pile vocale cohérente sans aucun
# fichier local. Ces valeurs sont donc la source de vérité, et `.env` ne fait
# que les surcharger.

DEFAULT_TTS_PROVIDER = "qwen3_local"
DEFAULT_TTS_MODEL_PATH = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-6bit"
DEFAULT_TTS_VOICE_PATH = "./voices/jarvis-fr"
DEFAULT_TTS_DEVICE = "auto"
DEFAULT_TTS_STREAMING = True
DEFAULT_TTS_SAMPLE_RATE = 24000
DEFAULT_TTS_CHANNELS = 1
DEFAULT_TTS_WARMUP = True
DEFAULT_TTS_TIMEOUT_SECONDS = 30.0

# Seuils de segmentation. Le minimum évite les fragments d'une syllabe, qui
# coûtent une génération complète pour un souffle ; le maximum borne le temps
# avant le premier son sur une phrase interminable.
DEFAULT_TTS_MIN_CHUNK_CHARS = 30
DEFAULT_TTS_TARGET_CHUNK_CHARS = 80
DEFAULT_TTS_MAX_CHUNK_CHARS = 180
DEFAULT_TTS_FLUSH_TIMEOUT_MS = 250

# Le **premier** segment obéit à des seuils plus courts que les suivants. Ce
# n'est pas une incohérence : il est le seul dont la longueur se paie en
# silence pur, pendant que l'utilisateur attend. Les suivants se synthétisent
# derrière une lecture déjà commencée, donc leur durée ne s'entend pas.
#
# Mesuré sur ce Mac mini M4, phrase de 94 caractères sans point interne :
# sans ces seuils, le premier son arrive à 564 ms — il faut synthétiser toute
# la phrase ; avec eux, la coupure tombe à la première virgule et le premier
# son arrive à ~200 ms.
DEFAULT_TTS_FIRST_CHUNK_MIN_CHARS = 15
DEFAULT_TTS_FIRST_CHUNK_MAX_CHARS = 60

# Fournisseurs connus. La fabrique refuse tout autre nom : c'est ce qui rend
# impossible l'apparition d'un backend distant par simple configuration.
# Un seul moteur est actif ; en ajouter un est un acte de code.
KNOWN_PROVIDERS: frozenset[str] = frozenset({"qwen3_local"})

# Nom de fichier attendus dans le répertoire de voix.
VOICE_METADATA_FILE = "metadata.json"
VOICE_REFERENCE_AUDIO = "reference.wav"
VOICE_REFERENCE_TEXT = "transcript.txt"
VOICE_REFERENCE_CACHE = "reference.npy"
VOICE_REFERENCE_CACHE_NPZ = "reference.npz"


@dataclass(frozen=True)
class TTSSettings:
    """Instantané des réglages TTS résolus."""

    provider: str
    model_path: str
    voice_path: str
    device: str
    streaming: bool
    sample_rate: int
    channels: int
    warmup: bool
    timeout_seconds: float
    min_chunk_chars: int
    target_chunk_chars: int
    max_chunk_chars: int
    flush_timeout_ms: int
    first_chunk_min_chars: int = DEFAULT_TTS_FIRST_CHUNK_MIN_CHARS
    first_chunk_max_chars: int = DEFAULT_TTS_FIRST_CHUNK_MAX_CHARS

    # ── Voix ────────────────────────────────────────────────────────────────

    @property
    def voice_dir(self) -> Path:
        return Path(self.voice_path).expanduser()

    @property
    def voice_id(self) -> str:
        """Identifiant lisible de la voix — le nom du répertoire."""
        name = self.voice_dir.name.strip()
        return name or "default"

    def reference_audio(self) -> Path | None:
        """Échantillon de référence, s'il a été déposé par l'utilisateur.

        Optionnel par construction : tant qu'aucune voix personnalisée n'est
        configurée, le backend utilise la voix par défaut du modèle plutôt que
        de refuser de parler.
        """
        path = self.voice_dir / VOICE_REFERENCE_AUDIO
        return path if path.is_file() else None

    def reference_text(self) -> str | None:
        """Transcript de l'échantillon — requis dès qu'il y a un échantillon.

        Le clonage vocal a besoin des deux : un audio sans transcript conduit
        le modèle à inventer l'alignement, et la voix dérive.
        """
        path = self.voice_dir / VOICE_REFERENCE_TEXT
        if not path.is_file():
            return None
        try:
            content = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return content or None

    def reference_cache(self) -> Path | None:
        """Tenseur float32 pré-encodé, s'il existe.

        Les deux formes écrites par ``scripts/prepare_jarvis_voice.py`` sont
        acceptées : ``reference.npy`` (échantillons seuls) et ``reference.npz``
        (échantillons + fréquence). Le sidecar sait lire les deux ; ne détecter
        que la première ferait perdre le clonage, sans message, à un profil qui
        n'aurait gardé que le ``.npz``.
        """
        for name in (VOICE_REFERENCE_CACHE, VOICE_REFERENCE_CACHE_NPZ):
            path = self.voice_dir / name
            if path.is_file():
                return path
        return None


def _config_value(name: str, fallback: object) -> object:
    """Lit ``config`` sans le rendre obligatoire (tests, outils autonomes)."""
    try:
        import config as app_config
    except Exception:  # pragma: no cover - dépend de l'environnement d'import
        return os.environ.get(name, fallback)
    return getattr(app_config, name, fallback)


def _as_bool(value: object, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off"}:
        return False
    return fallback


def _as_int(value: object, fallback: int, *, minimum: int = 1) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed >= minimum else fallback


def _as_float(value: object, fallback: float, *, minimum: float = 0.0) -> float:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > minimum else fallback


def load_tts_settings() -> TTSSettings:
    """Résout les réglages TTS depuis ``config`` (donc ``.env``) et les défauts.

    Les seuils de segmentation sont réordonnés si la configuration est
    incohérente (``min > max``) : un pipeline vocal ne doit pas se taire parce
    qu'une valeur a été saisie à l'envers.
    """
    provider = str(
        _config_value("TTS_PROVIDER", DEFAULT_TTS_PROVIDER) or DEFAULT_TTS_PROVIDER
    ).strip().lower()

    min_chars = _as_int(
        _config_value("TTS_MIN_CHUNK_CHARS", DEFAULT_TTS_MIN_CHUNK_CHARS),
        DEFAULT_TTS_MIN_CHUNK_CHARS,
    )
    target_chars = _as_int(
        _config_value("TTS_TARGET_CHUNK_CHARS", DEFAULT_TTS_TARGET_CHUNK_CHARS),
        DEFAULT_TTS_TARGET_CHUNK_CHARS,
    )
    max_chars = _as_int(
        _config_value("TTS_MAX_CHUNK_CHARS", DEFAULT_TTS_MAX_CHUNK_CHARS),
        DEFAULT_TTS_MAX_CHUNK_CHARS,
    )
    min_chars, target_chars, max_chars = sorted((min_chars, target_chars, max_chars))

    return TTSSettings(
        provider=provider,
        model_path=str(
            _config_value("TTS_MODEL_PATH", DEFAULT_TTS_MODEL_PATH) or DEFAULT_TTS_MODEL_PATH
        ).strip(),
        voice_path=str(
            _config_value("TTS_VOICE_PATH", DEFAULT_TTS_VOICE_PATH) or DEFAULT_TTS_VOICE_PATH
        ).strip(),
        device=str(
            _config_value("TTS_DEVICE", DEFAULT_TTS_DEVICE) or DEFAULT_TTS_DEVICE
        ).strip().lower(),
        streaming=_as_bool(
            _config_value("TTS_STREAMING", DEFAULT_TTS_STREAMING), DEFAULT_TTS_STREAMING
        ),
        sample_rate=_as_int(
            _config_value("TTS_SAMPLE_RATE", DEFAULT_TTS_SAMPLE_RATE),
            DEFAULT_TTS_SAMPLE_RATE,
            minimum=8000,
        ),
        channels=_as_int(
            _config_value("TTS_CHANNELS", DEFAULT_TTS_CHANNELS), DEFAULT_TTS_CHANNELS
        ),
        warmup=_as_bool(
            _config_value("TTS_WARMUP", DEFAULT_TTS_WARMUP), DEFAULT_TTS_WARMUP
        ),
        timeout_seconds=_as_float(
            _config_value("TTS_TIMEOUT_SECONDS", DEFAULT_TTS_TIMEOUT_SECONDS),
            DEFAULT_TTS_TIMEOUT_SECONDS,
        ),
        min_chunk_chars=min_chars,
        target_chunk_chars=target_chars,
        max_chunk_chars=max_chars,
        flush_timeout_ms=_as_int(
            _config_value("TTS_FLUSH_TIMEOUT_MS", DEFAULT_TTS_FLUSH_TIMEOUT_MS),
            DEFAULT_TTS_FLUSH_TIMEOUT_MS,
        ),
        first_chunk_min_chars=min(
            _as_int(
                _config_value(
                    "TTS_FIRST_CHUNK_MIN_CHARS", DEFAULT_TTS_FIRST_CHUNK_MIN_CHARS
                ),
                DEFAULT_TTS_FIRST_CHUNK_MIN_CHARS,
            ),
            min_chars,
        ),
        first_chunk_max_chars=min(
            _as_int(
                _config_value(
                    "TTS_FIRST_CHUNK_MAX_CHARS", DEFAULT_TTS_FIRST_CHUNK_MAX_CHARS
                ),
                DEFAULT_TTS_FIRST_CHUNK_MAX_CHARS,
            ),
            max_chars,
        ),
    )


__all__ = [
    "DEFAULT_TTS_CHANNELS",
    "DEFAULT_TTS_DEVICE",
    "DEFAULT_TTS_FLUSH_TIMEOUT_MS",
    "DEFAULT_TTS_MAX_CHUNK_CHARS",
    "DEFAULT_TTS_MIN_CHUNK_CHARS",
    "DEFAULT_TTS_MODEL_PATH",
    "DEFAULT_TTS_PROVIDER",
    "DEFAULT_TTS_SAMPLE_RATE",
    "DEFAULT_TTS_STREAMING",
    "DEFAULT_TTS_TARGET_CHUNK_CHARS",
    "DEFAULT_TTS_TIMEOUT_SECONDS",
    "DEFAULT_TTS_VOICE_PATH",
    "DEFAULT_TTS_WARMUP",
    "KNOWN_PROVIDERS",
    "TTSSettings",
    "VOICE_METADATA_FILE",
    "VOICE_REFERENCE_AUDIO",
    "VOICE_REFERENCE_CACHE",
    "VOICE_REFERENCE_CACHE_NPZ",
    "VOICE_REFERENCE_TEXT",
    "load_tts_settings",
]
