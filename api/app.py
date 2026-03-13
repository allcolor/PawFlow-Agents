"""PyFi2 REST API - FastAPI application.

Usage:
    python -m api.app                    # default port 8000
    python -m api.app --port 9000        # custom port
    python -m api.app --reload           # dev mode with auto-reload

Or with uvicorn directly:
    uvicorn api.app:app --reload --port 8000
"""

import argparse
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from tasks import register_all_tasks

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize services on startup, cleanup on shutdown."""
    # Startup
    register_all_tasks()
    logger.info("PyFi2 API started — tasks registered")

    # Crash recovery: restart flows that were running before
    try:
        from api.routers.execution_router import recover_flows_on_startup
        recover_flows_on_startup()
    except Exception as e:
        logger.error(f"Crash recovery failed: {e}")

    yield

    # Shutdown: save final state for all running executors
    try:
        from api.routers.execution_router import _continuous_executors, _executors_lock
        with _executors_lock:
            for flow_id, executor in _continuous_executors.items():
                if executor.is_running:
                    try:
                        executor.stop()
                    except Exception as e:
                        logger.error(f"Error stopping flow '{flow_id}': {e}")
    except Exception as e:
        logger.error(f"Shutdown cleanup error: {e}")

    logger.info("PyFi2 API shutting down")


app = FastAPI(
    title="PyFi2 API",
    description="REST API for PyFi2 — Apache NiFi-inspired data workflow framework",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — configurable origins (default: localhost Streamlit + API docs)
_cors_raw = os.environ.get("PYFI2_CORS_ORIGINS", "http://localhost:8501,http://localhost:8000")
cors_origins = [o.strip() for o in _cors_raw.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def validate_request(request: Request, call_next):
    """Input validation: limit request body size."""
    max_size = int(os.environ.get("PYFI2_MAX_BODY_SIZE", str(10 * 1024 * 1024)))
    cl = request.headers.get("content-length")
    if cl and int(cl) > max_size:
        return JSONResponse(status_code=413, content={"detail": "Request body too large"})
    response = await call_next(request)
    return response


# Rate limiting (100 requests/minute per IP by default)
if os.environ.get("PYFI2_RATE_LIMIT", "").lower() in ("1", "true", "yes"):
    from api.rate_limit import RateLimitMiddleware
    rate_limit = int(os.environ.get("PYFI2_RATE_LIMIT_MAX", "100"))
    rate_window = int(os.environ.get("PYFI2_RATE_LIMIT_WINDOW", "60"))
    app.add_middleware(RateLimitMiddleware, max_requests=rate_limit, window_seconds=rate_window)
    logger.info(f"Rate limiting enabled: {rate_limit} req/{rate_window}s")

# Register all routers
from api.routers import (
    auth_router,
    flows_router,
    execution_router,
    monitoring_router,
    tasks_router,
    workers_router,
    plugins_router,
    system_router,
)
from api.routers import ws_router
from api.routers.triggers_router import router as triggers_router

app.include_router(auth_router.router, prefix="/api/v1/auth", tags=["Authentication"])
app.include_router(flows_router.router, prefix="/api/v1/flows", tags=["Flows"])
app.include_router(execution_router.router, prefix="/api/v1/execution", tags=["Execution"])
app.include_router(monitoring_router.router, prefix="/api/v1/monitoring", tags=["Monitoring"])
app.include_router(tasks_router.router, prefix="/api/v1/tasks", tags=["Tasks & Services"])
app.include_router(workers_router.router, prefix="/api/v1/workers", tags=["Workers"])
app.include_router(plugins_router.router, prefix="/api/v1/plugins", tags=["Plugins"])
app.include_router(system_router.router, prefix="/api/v1/system", tags=["System"])
app.include_router(ws_router.router, prefix="/ws", tags=["WebSocket"])
app.include_router(triggers_router, prefix="/api/v1/triggers", tags=["Triggers"])


@app.get("/", tags=["Root"])
def root():
    """API root — health check."""
    return {
        "name": "PyFi2 API",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
    }


if __name__ == "__main__":
    import sys, warnings
    if sys.platform == "win32":
        import asyncio
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    import uvicorn

    parser = argparse.ArgumentParser(description="PyFi2 REST API")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        "api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
