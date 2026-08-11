"""Documentation développeur locale, protégée par la session JARVIS."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from api.openapi import render_openapi_docs
from security_headers import build_content_security_policy, inline_csp_hashes

router = APIRouter(tags=["developer"])


@router.get("/api/developer/openapi.json")
async def api_developer_openapi(request: Request) -> JSONResponse:
    """Retourne le contrat exact de l'instance, après authentification."""
    return JSONResponse(
        request.app.openapi(),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/developer/docs")
async def api_developer_docs(request: Request) -> HTMLResponse:
    """Rend un catalogue autonome des opérations sans dépendance CDN."""
    content = render_openapi_docs(request.app.openapi())
    script_hashes, style_hashes = inline_csp_hashes(content)
    return HTMLResponse(
        content,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": build_content_security_policy(
                script_hashes=script_hashes,
                style_hashes=style_hashes,
            ),
        },
    )
