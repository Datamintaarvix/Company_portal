"""Company Employee Portal - Main FastAPI Application.

Entry point for the application. Configures the FastAPI app, registers lifespan
handlers, and defines core root and health check endpoints.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional
from fastapi import FastAPI, HTTPException, status
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from app.core.config import get_settings
from app.db.session import check_db_connection
from app.routers.admin import router as admin_router
from app.routers.attendance import router as attendance_router
from app.routers.auth import router as auth_router
from app.routers.employees import router as employees_router
from app.routers.hr import router as hr_router
from app.routers.leaves import router as leaves_router
from app.routers.views import router as views_router
from app.schemas.health import DatabaseHealthResponse, HealthResponse, RootResponse as BaseRootResponse


class RootResponse(BaseRootResponse):
    """Root status response schema with optional docs_url for production hardening."""

    docs_url: Optional[str] = Field(default=None, examples=["/docs"])

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("company_portal")

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup and shutdown procedures."""
    logger.info("Starting up %s (v%s) in [%s] mode...", settings.PROJECT_NAME, settings.VERSION, settings.ENVIRONMENT)
    db_ok = check_db_connection()
    if db_ok:
        logger.info("PostgreSQL database connection verified successfully.")
    else:
        logger.warning("PostgreSQL database connection check failed at startup. Will retry on request.")
    yield
    logger.info("Shutting down %s...", settings.PROJECT_NAME)


# Create FastAPI application instance
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Secure internal portal for Employee, HR, and Admin workflows.",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
    lifespan=lifespan,
)

# Mount static assets with absolute path resolution for serverless runtime safety
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Register routers
app.include_router(views_router)
app.include_router(auth_router)
app.include_router(employees_router)
app.include_router(admin_router)
app.include_router(hr_router)
app.include_router(attendance_router)
app.include_router(leaves_router)


@app.get(
    "/",
    response_model=RootResponse,
    status_code=status.HTTP_200_OK,
    tags=["General"],
    summary="Portal Welcome & API Info",
)
async def root() -> RootResponse:
    """Return a welcome message and links to API documentation and health status."""
    return RootResponse(
        message=f"Welcome to {settings.PROJECT_NAME}",
        docs_url=None if settings.is_production else "/docs",
        health_url="/health",
        version=settings.VERSION,
    )


@app.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    tags=["System"],
    summary="Service Health Check",
)
async def health_check() -> HealthResponse:
    """Return the health status of the service."""
    return HealthResponse(
        status="healthy",
        service="company-portal",
        environment=settings.ENVIRONMENT,
        version=settings.VERSION,
    )


@app.get(
    "/health/db",
    response_model=DatabaseHealthResponse,
    status_code=status.HTTP_200_OK,
    tags=["System"],
    summary="Database Connectivity Check",
    responses={
        503: {
            "description": "Database Unavailable",
            "content": {
                "application/json": {
                    "example": {
                        "detail": {
                            "status": "unhealthy",
                            "database": "disconnected",
                            "error": "Database service is unavailable",
                        }
                    }
                }
            },
        }
    },
)
async def health_check_db() -> Any:
    """Perform a lightweight database connectivity check (SELECT 1).

    Returns:
        HTTP 200 with connection info when reachable.
        HTTP 503 when the database is unreachable, with no sensitive credentials exposed.
    """
    is_connected = check_db_connection()
    if not is_connected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "unhealthy",
                "database": "disconnected",
                "error": "Database service is unavailable",
            },
        )

    return DatabaseHealthResponse(
        status="healthy",
        database="connected",
        database_name=settings.POSTGRES_DB,
        environment=settings.ENVIRONMENT,
    )
