"""Mount professional API routers onto the existing FastAPI app."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from dashboard.app.core.logging import setup_logging
from dashboard.app.modules.api_keys.router import router as api_keys_router
from dashboard.app.modules.audit.router import router as audit_router
from dashboard.app.modules.auth.router import router as auth_router
from dashboard.app.modules.observability.router import router as observability_router
from dashboard.app.modules.pricing.router import router as pricing_router
from dashboard.app.modules.realtime.router import router as realtime_router
from dashboard.app.modules.system.router import router as system_router
from dashboard.app.modules.tasks.router import router as tasks_router
from dashboard.app.modules.users.router import router as users_router

logger = setup_logging("private_api.mount")


def mount_professional_api(app: FastAPI) -> None:
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(observability_router)
    app.include_router(tasks_router)
    app.include_router(api_keys_router)
    app.include_router(realtime_router)
    app.include_router(system_router)
    app.include_router(audit_router)
    app.include_router(pricing_router)

    # React build (if present)
    frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if frontend_dist.is_dir() and (frontend_dist / "index.html").is_file():
        assets = frontend_dist / "assets"
        if assets.is_dir():
            app.mount("/console/assets", StaticFiles(directory=str(assets)), name="console-assets")

        @app.get("/console")
        @app.get("/console/")
        @app.get("/console/{full_path:path}")
        async def console_spa(full_path: str = ""):
            # never hijack API
            if full_path.startswith("api/"):
                return {"detail": "not found"}
            index = frontend_dist / "index.html"
            return FileResponse(index)

        logger.info("React console mounted at /console from %s", frontend_dist)


async def run_bootstrap_safe() -> None:
    try:
        from dashboard.app.services.bootstrap import bootstrap_all

        report = await bootstrap_all()
        logger.info("bootstrap report: schema=%s admin=%s", report.get("schema"), report.get("admin"))
    except Exception as exc:
        logger.warning("bootstrap skipped/failed (billing open mode may continue): %s", exc)
