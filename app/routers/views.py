"""Frontend view routers serving HTML templates for Login and Dashboard."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["Frontend Views"], include_in_schema=False)
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Render the corporate login page."""
    return templates.TemplateResponse(request=request, name="login.html")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """Render the authenticated employee portal dashboard."""
    return templates.TemplateResponse(request=request, name="dashboard.html")


@router.get("/portal", response_class=RedirectResponse)
async def portal_redirect():
    """Convenience redirect to the dashboard view."""
    return RedirectResponse(url="/dashboard", status_code=302)
