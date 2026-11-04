"""Frontend view routers serving HTML templates for Login and Dashboard."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.config import get_settings

router = APIRouter(tags=["Frontend Views"], include_in_schema=False)
templates = Jinja2Templates(directory="app/templates")
settings = get_settings()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Render the corporate login page."""
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"csrf_cookie_name": settings.CSRF_COOKIE_NAME},
    )


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """Render the authenticated employee portal dashboard."""
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"csrf_cookie_name": settings.CSRF_COOKIE_NAME},
    )


@router.get("/portal", response_class=RedirectResponse)
async def portal_redirect():
    """Convenience redirect to the dashboard view."""
    return RedirectResponse(url="/dashboard", status_code=302)
