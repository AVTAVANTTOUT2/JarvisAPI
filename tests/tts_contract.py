"""Contrat de payload audio, partagé par les tests TTS mockés et réseau.

Une seule définition de « ce qu'est un MP3 Edge valide » : le test unitaire
(edge-tts simulé) et le test réseau réel appliquent exactement la même
exigence. Sans ce partage, l'un des deux finit toujours par être plus laxiste
que l'autre.

Fonctions pures, sans dépendance à pytest : elles sont elles-mêmes testées
dans `tests/test_tts_edge_unit.py`, ce qui prouve qu'un audio invalide
(WAV Kokoro, M4A macOS, flux tronqué, réponse vide) fait bien échouer un test
au lieu d'être ignoré.
"""

from __future__ import annotations

from typing import Final

ID3_MAGIC: Final[bytes] = b"ID3"
RIFF_MAGIC: Final[bytes] = b"RIFF"  # WAV — Kokoro, macOS natif
OGG_MAGIC: Final[bytes] = b"OggS"
FTYP_MAGIC: Final[bytes] = b"ftyp"  # M4A/AAC — macOS say + afconvert
FTYP_OFFSET: Final[int] = 4

# Une phrase française courte pèse plusieurs kilo-octets en MP3 24 kHz.
# En dessous, on tient un en-tête sans audio, pas une synthèse.
MIN_MP3_BYTES: Final[int] = 1000

CONTAINER_MP3: Final[str] = "mp3"
CONTAINER_WAV: Final[str] = "wav"
CONTAINER_M4A: Final[str] = "m4a"
CONTAINER_OGG: Final[str] = "ogg"
CONTAINER_EMPTY: Final[str] = "vide"
CONTAINER_UNKNOWN: Final[str] = "inconnu"


def has_mpeg_frame_sync(data: bytes) -> bool:
    """`True` si les octets commencent par une synchro de trame MPEG (0xFFEx)."""
    return len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0


def detect_container(data: bytes) -> str:
    """Identifie le conteneur audio par ses nombres magiques."""
    if not data:
        return CONTAINER_EMPTY
    if data.startswith(ID3_MAGIC) or has_mpeg_frame_sync(data):
        return CONTAINER_MP3
    if data.startswith(RIFF_MAGIC):
        return CONTAINER_WAV
    if data.startswith(OGG_MAGIC):
        return CONTAINER_OGG
    if data[FTYP_OFFSET : FTYP_OFFSET + len(FTYP_MAGIC)] == FTYP_MAGIC:
        return CONTAINER_M4A
    return CONTAINER_UNKNOWN


def describe_payload(data: bytes) -> str:
    """Résumé lisible pour un message d'échec : conteneur, taille, premiers octets."""
    prefix = data[:8].hex(" ") if data else "—"
    return f"conteneur={detect_container(data)} taille={len(data)} premiers_octets={prefix}"


def mp3_payload_violations(data: bytes, *, min_bytes: int = MIN_MP3_BYTES) -> list[str]:
    """Liste des manquements au contrat MP3 ; liste vide = payload conforme."""
    violations: list[str] = []
    if not data:
        violations.append("payload vide (aucun octet audio reçu)")
        return violations

    container = detect_container(data)
    if container != CONTAINER_MP3:
        violations.append(
            f"conteneur {container} au lieu de MP3 "
            "(un moteur local a répondu à la place d'Edge, ou le format a changé)"
        )
    if len(data) < min_bytes:
        violations.append(f"taille {len(data)} octets < minimum attendu {min_bytes}")
    return violations


def assert_mp3_payload(
    data: bytes, *, min_bytes: int = MIN_MP3_BYTES, source: str = "TTS Edge"
) -> None:
    """Échoue si `data` n'est pas un MP3 plausible produit par `source`."""
    violations = mp3_payload_violations(data, min_bytes=min_bytes)
    if violations:
        raise AssertionError(
            f"Payload {source} invalide : "
            + " ; ".join(violations)
            + f" [{describe_payload(data)}]"
        )


__all__ = [
    "CONTAINER_EMPTY",
    "CONTAINER_M4A",
    "CONTAINER_MP3",
    "CONTAINER_OGG",
    "CONTAINER_UNKNOWN",
    "CONTAINER_WAV",
    "MIN_MP3_BYTES",
    "assert_mp3_payload",
    "describe_payload",
    "detect_container",
    "has_mpeg_frame_sync",
    "mp3_payload_violations",
]
