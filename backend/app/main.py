"""FastAPI entry point: serves the dashboard and the GitHub webhook receiver."""

import sys
from pathlib import Path
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
import structlog
import uvicorn

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import get_settings
from app.models.database import init_db
from app.routes import dashboard_router, webhook_router, agent_router


def configure_logging():
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


_DEFAULT_SECRET_KEY = "change-this-to-a-random-secret-key-in-production"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger = structlog.get_logger()
    settings = get_settings()
    logger.info("application_starting", app=settings.APP_NAME, version=settings.APP_VERSION)

    if settings.SECRET_KEY == _DEFAULT_SECRET_KEY:
        logger.warning(
            "insecure_default_secret_key",
            message="SECRET_KEY is still the default placeholder. Set a random "
            "SECRET_KEY in your environment before deploying to production — "
            "it's used to sign session tokens and derive the API key encryption key.",
        )

    Path("./data").mkdir(parents=True, exist_ok=True)
    Path(settings.KEYS_DIR).mkdir(parents=True, exist_ok=True)

    init_db(settings.DATABASE_URL)
    logger.info("application_started", host=settings.HOST, port=settings.PORT)

    yield

    logger.info("application_shutting_down")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="AI-powered GitHub Issue-to-PR Autopilot built for the Global AI Hackathon with Qwen Cloud.",
        lifespan=lifespan,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url=None,
    )

    cors_origins = [o.strip() for o in settings.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(dashboard_router)
    app.include_router(webhook_router)
    app.include_router(agent_router)

    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

    @app.get("/", response_class=HTMLResponse)
    async def landing(request: Request):
        return templates.TemplateResponse("landing.html", {
            "request": request, "app_name": settings.APP_NAME, "app_version": settings.APP_VERSION,
        })

    @app.get("/health")
    async def health_check():
        return JSONResponse({
            "status": "healthy",
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        wants_html = "text/html" in request.headers.get("accept", "")
        if exc.status_code == 401 and request.url.path.startswith("/dashboard") and wants_html:
            return RedirectResponse(url="/dashboard/login")
        if exc.status_code == 404:
            return JSONResponse({"error": "Not found", "path": request.url.path}, status_code=404)
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    return app


app = create_app()


if __name__ == "__main__":
    configure_logging()
    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
