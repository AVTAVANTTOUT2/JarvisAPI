"""Recherche sémantique locale — sentence-transformers + similarité cosinus.

Le volume de données personnelles (quelques milliers d'épisodes/
enregistrements au plus) rend une recherche par similarité cosinus en
mémoire largement suffisante — pas besoin d'un moteur vectoriel dédié.

`sentence-transformers` est une dépendance lourde optionnelle (comme torch
ou faster-whisper ailleurs dans le projet) : importée paresseusement, elle
ne casse jamais l'import de ce module si elle est absente — seule la
recherche elle-même échoue alors avec un message clair.

Note de vérification : le téléchargement initial du modèle nécessite un
accès internet vers huggingface.co, indisponible dans certains
environnements réseau restreints (sandbox de développement compris) — sur
la machine de déploiement finale (accès internet normal), le modèle se
télécharge une fois puis reste en cache local, aucun appel réseau ensuite.
"""

from __future__ import annotations

import logging

import numpy as np

import config

logger = logging.getLogger(__name__)

_model = None


class SemanticSearchUnavailable(RuntimeError):
    """`sentence-transformers` non installé, ou modèle impossible à charger."""


def _get_model():
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise SemanticSearchUnavailable(
                "sentence-transformers non installé — pip install sentence-transformers"
            ) from e
        try:
            _model = SentenceTransformer(config.SEMANTIC_SEARCH_MODEL)
        except Exception as e:
            raise SemanticSearchUnavailable(f"Impossible de charger le modèle : {e}") from e
    return _model


def embed_text(text: str) -> np.ndarray:
    """Vecteur d'embedding normalisé (norme 1) pour `text`."""
    model = _get_model()
    return model.encode(text, convert_to_numpy=True, normalize_embeddings=True)


def embedding_to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def blob_to_embedding(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


def index_text(source_type: str, source_id: int, text: str) -> None:
    """Calcule et stocke l'embedding d'un texte (episode/recording). Best-effort."""
    from database import upsert_memory_embedding

    if not text or not text.strip():
        return
    vec = embed_text(text)
    upsert_memory_embedding(
        source_type, source_id, text.strip()[:300], embedding_to_blob(vec), config.SEMANTIC_SEARCH_MODEL
    )


def semantic_search(query: str, limit: int = 10, source_type: str | None = None) -> list[dict]:
    """Entrées les plus proches sémantiquement de `query`, triées par score décroissant."""
    from database import get_all_memory_embeddings

    query_vec = embed_text(query)
    rows = get_all_memory_embeddings(source_type=source_type)

    scored = []
    for row in rows:
        vec = blob_to_embedding(row["embedding"])
        score = cosine_similarity(query_vec, vec)
        scored.append({
            "source_type": row["source_type"],
            "source_id": row["source_id"],
            "text_preview": row["text_preview"],
            "score": round(score, 4),
        })
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:limit]
