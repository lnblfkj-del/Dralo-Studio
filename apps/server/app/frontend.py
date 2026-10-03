"""Serve only the compiled creator UI; source, database and media are never mounted."""

from pathlib import Path
import json

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles


class ArcadeStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if Path(path).parts == ("dist", "box2d.wasm.wasm"):
            response.headers["Access-Control-Allow-Origin"] = "*"
        return response


def select_frontend_dist(web: Path, *, execution_location: str = "local") -> Path:
    standalone = web / "dist-standalone"
    if execution_location == "local" and (standalone / "index.html").is_file():
        return standalone
    return web / "dist"


def register_frontend(app: FastAPI, dist: Path, *, execution_location: str = "local") -> None:
    if not (dist / "index.html").is_file():
        return

    manifest = dist / "edition-manifest.json"
    edition = json.loads(manifest.read_text(encoding="utf-8")).get("edition") if manifest.is_file() else None
    if edition not in {None, "standalone", "cloud-capable"}:
        raise RuntimeError("Unknown frontend build edition")
    if edition == "standalone" and execution_location == "cloud":
        raise RuntimeError("Cloud deployment cannot serve a standalone frontend build")

    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="frontend-assets")
    if (dist / "director-upstream" / "index.html").is_file():
        app.mount("/director-upstream", StaticFiles(directory=dist / "director-upstream", html=True), name="director-upstream")
    if (dist / "__ui" / "arcade" / "games" / "matchThree.html").is_file():
        app.mount("/__ui/arcade", ArcadeStaticFiles(directory=dist / "__ui" / "arcade"), name="arcade-preview")

    async def index():
        if execution_location == "cloud":
            source = (dist / "index.html").read_text(encoding="utf-8")
            return HTMLResponse(
                source.replace("</head>", '<meta name="drama-deployment" content="cloud"></head>', 1),
                headers={"Cache-Control": "no-store"},
            )
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-store"})

    routes = (
        "/",
        "/login",
        "/__ui/games",
        "/projects",
        "/imports/{import_session_id:int}/review",
        "/asset-center",
        "/history",
        "/tasks",
        "/settings/providers",
        "/settings/execution",
        "/settings/storage",
        "/settings/users",
        "/settings/profile",
        "/settings/admin",
        "/settings/support",
        "/settings/ai",
        "/settings/ai/defaults",
        "/settings/ai/skills",
        "/settings/agents",
        "/settings/model-routing",
        "/settings/skills",
        "/settings/styles",
        "/market-research",
        "/market-research/{run_id:int}",
        "/creation/{session_id:int}/story-bible",
        "/projects/{project_id:int}",
        "/projects/{project_id:int}/outline",
        "/projects/{project_id:int}/canvas",
        "/projects/{project_id:int}/assets",
        "/projects/{project_id:int}/storyboard",
        "/projects/{project_id:int}/episode-videos",
        "/projects/{project_id:int}/episodes/{episode_id:int}/storyboard",
        "/projects/{project_id:int}/episodes/{episode_id:int}/studio",
    )
    for route in routes:
        if edition == "standalone" and route in {"/settings/admin", "/settings/support"}:
            continue
        app.add_api_route(route, index, methods=["GET"], include_in_schema=False)

    if (dist / "favicon.svg").is_file():
        async def favicon():
            return FileResponse(
                dist / "favicon.svg",
                media_type="image/svg+xml",
                headers={"Cache-Control": "public, max-age=86400"},
            )

        app.add_api_route("/favicon.svg", favicon, methods=["GET"], include_in_schema=False)

    if (dist / "version.json").is_file():
        async def version():
            return FileResponse(dist / "version.json", headers={"Cache-Control": "no-store"})

        app.add_api_route("/version.json", version, methods=["GET"], include_in_schema=False)
