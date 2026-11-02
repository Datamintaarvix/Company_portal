"""API and view routers."""

from app.routers.admin import router as admin_router
from app.routers.attendance import router as attendance_router
from app.routers.auth import router as auth_router
from app.routers.employees import router as employees_router
from app.routers.hr import router as hr_router
from app.routers.leaves import router as leaves_router
from app.routers.views import router as views_router

__all__ = [
    "auth_router",
    "employees_router",
    "admin_router",
    "hr_router",
    "attendance_router",
    "leaves_router",
    "views_router",
]
