"""Public, non-secret contract for the official cloud desktop client."""

from fastapi import APIRouter, Response

router = APIRouter(prefix="/client", tags=["client"])


@router.get("/capabilities")
async def capabilities(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {
        "product": "dralo-studio",
        "edition": "cloud",
        "desktop_protocol": {"minimum": 1, "maximum": 1},
        "authentication": "cookie-single-terminal",
        "media_authorization": "online-per-read",
        "task_execution": "server-independent",
    }
