"""Contrats du moteur PDF local et de sa frontière Python 3.14."""

from __future__ import annotations

import subprocess
import sys

from api.misc_files import _extract_text_from_upload
from jarvis.pdf_runtime import pymupdf


def test_pdf_runtime_import_is_clean_with_deprecations_as_errors() -> None:
    probe = subprocess.run(
        [
            sys.executable,
            "-W",
            "error::DeprecationWarning",
            "-c",
            "from jarvis.pdf_runtime import pymupdf; print(pymupdf.__version__)",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip().startswith("1.28.")
    assert probe.stderr == ""


def test_school_pdf_extraction_uses_a_closed_document(tmp_path) -> None:
    path = tmp_path / "cours.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Cours JARVIS local")
        document.save(path)

    text, document_type = _extract_text_from_upload(path)

    assert "Cours JARVIS local" in text
    assert document_type == "cours"
