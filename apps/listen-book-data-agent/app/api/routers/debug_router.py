from pathlib import Path

from fastapi import APIRouter
from starlette.responses import HTMLResponse

debug_router = APIRouter(tags=["查询调试"])

_DEBUG_PAGE = Path(__file__).parents[1] / "static" / "debug.html"


@debug_router.get("/debug", response_class=HTMLResponse)
async def debug_page() -> HTMLResponse:
    """Serve the built-in SSE query inspector without an additional frontend build."""

    return HTMLResponse(_DEBUG_PAGE.read_text(encoding="utf-8"))
