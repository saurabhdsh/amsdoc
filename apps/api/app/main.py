"""ASGI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import init_db
from app.errors import AppError
from app.events import bus
from app.routers.http import router
from app.seed import seed

log = logging.getLogger("ams")


def create_app() -> FastAPI:
    settings = get_settings()
    settings.assert_production_safe()
    init_db(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import asyncio

        from app import database as database_module

        bus.bind(asyncio.get_running_loop())
        db = database_module.SessionLocal()
        try:
            seed(db)
            db.commit()
        finally:
            db.close()
        yield

    application = FastAPI(
        title="AI for AMS",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None if settings.environment == "production" else "/docs",
        redoc_url=None,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.middleware("http")
    async def request_context(request, call_next):
        import time
        import uuid

        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["x-frame-options"] = "DENY"
        response.headers["referrer-policy"] = "no-referrer"
        response.headers["cache-control"] = "no-store"
        log.info("%s %s %s %.0fms", request.method, request.url.path, response.status_code, (time.perf_counter() - started) * 1000)
        return response

    @application.exception_handler(AppError)
    async def app_error(_request, exc: AppError):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}})

    @application.get("/healthz")
    def healthz():
        return {"status": "ok"}

    application.include_router(router)
    _configure_telemetry(application, settings.otel_exporter_otlp_endpoint)
    return application


def _configure_telemetry(application: FastAPI, endpoint: str) -> None:
    if not endpoint:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(application)
    except Exception:
        log.warning("OpenTelemetry instrumentation is unavailable")
