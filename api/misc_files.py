"""Handlers d'upload et de téléchargement des productions."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import fitz
from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse

import config
from database import save_school_document

logger = logging.getLogger("jarvis")



PDF_EXT = {".pdf"}
TEXT_EXT = {".txt", ".md"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def _extract_text_from_upload(filepath: Path) -> tuple[str, str]:
    """Extrait le texte d'un fichier uploadé. Retourne (texte, doc_type)."""
    ext = filepath.suffix.lower()

    if ext in PDF_EXT:
        try:
            doc = fitz.open(str(filepath))
            text = "\n\n".join(page.get_text() for page in doc)
            doc.close()
            return text.strip(), "cours"
        except Exception as e:
            logger.error(f"Erreur extraction PDF {filepath.name} : {e}")
            return "", "cours"

    if ext in TEXT_EXT:
        try:
            return filepath.read_text(encoding="utf-8", errors="replace").strip(), "cours"
        except Exception as e:
            logger.error(f"Erreur lecture texte {filepath.name} : {e}")
            return "", "cours"

    if ext in IMAGE_EXT:
        # OCR à brancher en Phase 4 (Tesseract ou Claude vision)
        return "", "image"

    return "", "autre"


async def upload(file: UploadFile):
    """Upload d'un document scolaire.

    - PDF : extraction texte via pymupdf (`fitz`)
    - .txt / .md : lecture directe
    - images : sauvegarde brute (OCR à venir)

    Le document est référencé dans `school_documents` (titre = nom sans extension,
    content = texte extrait, doc_type, file_path).
    """
    upload_dir = Path(config.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    if not file.filename:
        raise HTTPException(400, "Nom de fichier manquant")

    safe_name = Path(file.filename).name  # vire les / éventuels
    dest = upload_dir / safe_name
    try:
        content = await file.read()
        dest.write_bytes(content)
    except Exception as e:
        logger.error(f"Erreur écriture upload {safe_name} : {e}")
        raise HTTPException(500, f"Échec écriture : {e}")

    text, doc_type = _extract_text_from_upload(dest)
    title = dest.stem

    try:
        doc_id = save_school_document(
            title=title, content=text, doc_type=doc_type, file_path=str(dest),
        )
    except Exception as e:
        logger.error(f"Erreur DB upload {safe_name} : {e}")
        doc_id = None

    logger.info(
        f"Upload : {safe_name} ({len(content)} octets, "
        f"texte extrait : {len(text)} chars, doc_id={doc_id})"
    )

    return {
        "status": "ok",
        "filename": safe_name,
        "size": len(content),
        "content_length": len(text),
        "doc_type": doc_type,
        "doc_id": doc_id,
    }


def _outputs_root() -> Path:
    """Racine résolue pour les fichiers servis par /api/outputs."""
    return Path(config.SCHOOL_OUTPUT_DIR).resolve().parent  # data/outputs/


async def api_outputs_list():
    """Liste tous les fichiers produits dans data/outputs/school/ (récursif).

    Retourne pour chaque fichier : filename, subject (sous-dossier), path relatif,
    size_kb, created_at (mtime ISO).
    """
    school_dir = Path(config.SCHOOL_OUTPUT_DIR)
    school_dir.mkdir(parents=True, exist_ok=True)
    root = _outputs_root()

    files = []
    for path in school_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            relative = path.resolve().relative_to(root)
            # Le sous-dossier directement sous school/ = la matière
            try:
                subject = path.resolve().relative_to(school_dir.resolve()).parts[0]
            except (ValueError, IndexError):
                subject = "divers"
            files.append({
                "filename": path.name,
                "subject": subject,
                "path": str(relative),
                "size_kb": round(stat.st_size / 1024, 2),
                "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            })
        except Exception as e:
            logger.warning(f"Skip {path} : {e}")

    files.sort(key=lambda f: f["created_at"], reverse=True)
    return {"files": files, "count": len(files)}


async def api_outputs_download(filepath: str):
    """Télécharge un fichier produit. `filepath` est relatif à data/outputs/.

    Sécurité : le chemin résolu doit rester sous data/outputs/ (anti path traversal).
    """
    root = _outputs_root()
    try:
        target = (root / filepath).resolve()
    except Exception:
        raise HTTPException(400, "Chemin invalide")

    # Protection path traversal
    try:
        target.relative_to(root)
    except ValueError:
        logger.warning(f"Path traversal bloqué : {filepath}")
        raise HTTPException(403, "Chemin hors du dossier autorisé")

    if not target.exists() or not target.is_file():
        raise HTTPException(404, f"Fichier introuvable : {filepath}")

    return FileResponse(target, filename=target.name)


