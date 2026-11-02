"""Schemas for health and root endpoints."""

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Health check response schema."""

    status: str = Field(..., examples=["healthy"], description="Overall health status")
    service: str = Field(..., examples=["company-portal"], description="Service identifier")
    environment: str = Field(..., examples=["development"], description="Current operating environment")
    version: str = Field(..., examples=["0.1.0"], description="Application version")


class RootResponse(BaseModel):
    """Root status response schema."""

    message: str = Field(..., examples=["Welcome to Company Employee Portal API"])
    docs_url: str = Field(..., examples=["/docs"])
    health_url: str = Field(..., examples=["/health"])
    version: str = Field(..., examples=["0.1.0"])


class DatabaseHealthResponse(BaseModel):
    """Database connectivity health check response schema."""

    status: str = Field(..., examples=["healthy"], description="Database connection status")
    database: str = Field(..., examples=["connected"], description="Database reachability state")
    database_name: str = Field(..., examples=["company_portal"], description="Connected database name")
    environment: str = Field(..., examples=["development"], description="Application environment")
