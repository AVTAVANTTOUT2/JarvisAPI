"""Réponses HTML statiques liées à leur contenu par CSP SHA-256."""

from __future__ import annotations

from pathlib import Path

from core.html_security import secure_html_file_response
from security_headers import inline_csp_hashes


def test_secure_html_file_response_sets_content_specific_policy(tmp_path: Path):
    page = tmp_path / "index.html"
    page.write_text(
        '<!doctype html><script>window.__BOOTSTRAP__ = "safe"</script>',
        encoding="utf-8",
    )

    response = secure_html_file_response(page, headers={"Cache-Control": "no-cache"})
    script_hashes, _ = inline_csp_hashes(page.read_bytes())

    assert response.headers["cache-control"] == "no-cache"
    assert script_hashes[0] in response.headers["content-security-policy"]
    assert "'unsafe-inline'" not in response.headers["content-security-policy"].split(
        "style-src-attr", 1
    )[0]


def test_external_script_does_not_add_a_hash(tmp_path: Path):
    page = tmp_path / "index.html"
    page.write_text('<script type="module" src="/app.js"></script>', encoding="utf-8")

    response = secure_html_file_response(page)

    assert "sha256-" not in response.headers["content-security-policy"]
