# Company Employee Portal — Secure Backend & Portal

A production-minded internal company employee portal built with **FastAPI**, **PostgreSQL**, **SQLAlchemy 2.x**, **Alembic**, and **Pydantic**. Designed with strict security standards, role-based access control (RBAC for Employee, HR, and Admin), and automated tests.

---

## Current Status: Stage 5 Complete

- [x] **Stage 1 — Project setup**: Project structure, virtual environment, configuration management, base FastAPI app with `/` and `/health`, and automated tests.
- [x] **Stage 2 — PostgreSQL integration**: PostgreSQL 16 connection pooling (`psycopg` binary dialect), SQLAlchemy 2.x ORM models, Alembic migrations, database constraints, and `/health/db` connectivity endpoint.
- [x] **Stage 3 — Authentication**: Secure Argon2id password hashing (`pwdlib`), PostgreSQL server-side sessions, sliding-window login rate limiting, CSRF protection for cookie-authenticated requests, interactive CLI provisioning (`python -m app.cli`).
- [x] **Stage 4 — Role-based access control (RBAC)**: Backend permission dependencies (`RequireRole`) for Employee, HR, and Admin; directory pagination; record access control; role/status administration; and session revocation upon deactivation.
- [x] **Stage 5 — Frontend**: Professional, responsive corporate web portal (clean HTML5, modern CSS design system, vanilla JS) with role-aware navigation, interactive directory, self-service profile editing, staff provisioning, and admin modals.
- [ ] **Stage 6 — Comprehensive automated test suite**: Unit and integration tests for security edge cases.
- [ ] **Stage 7 — Docker & deployment readiness**: Dockerfile, compose, production hardening.

---

## Project Structure

```text
company-portal/
├── alembic/                 # Database migration scripts
│   ├── versions/            # Versioned migration revisions
│   │   └── 5a22a4bd6762_create_employees_table.py
│   └── env.py               # Alembic configuration connecting to models & settings
├── alembic.ini              # Alembic project configuration file
├── docker-compose.yml       # Local PostgreSQL 16 database service
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI application entrypoint & root/health routes
│   ├── core/
│   │   ├── __init__.py
│   │   └── config.py        # Centralized Pydantic settings loading from .env
│   ├── db/
│   │   ├── __init__.py
│   │   ├── base.py          # SQLAlchemy 2.0 DeclarativeBase
│   │   └── session.py       # Engine with pool_pre_ping, SessionLocal & get_db
│   ├── models/
│   │   ├── __init__.py      # Model exports
│   │   └── employee.py      # Employee model with constraints & UserRole enum
│   ├── schemas/
│   │   ├── __init__.py
│   │   └── health.py        # Typed response schemas for /, /health, /health/db
│   ├── routers/             # API route controllers (Stage 3+)
│   ├── services/            # Business logic layer (Stage 3+)
│   ├── templates/           # Server-side HTML templates (Stage 5)
│   └── static/              # CSS, JavaScript, and assets (Stage 5)
├── tests/
│   ├── __init__.py
│   ├── test_database.py     # Database connectivity & Employee model constraint tests
│   └── test_health.py       # Root, health, docs, and 404 endpoint tests
├── .env.example             # Template for environment variables
├── .env                     # Local environment settings (git-ignored)
├── .gitignore               # Ignored files (virtual envs, secrets, cache)
├── requirements.txt         # Pinned Python package dependencies
└── README.md                # Project documentation and guide
```

---

## Getting Started

### 1. Prerequisites
- Python 3.10+ (Python 3.10.0 is currently configured)
- Docker Desktop (or local PostgreSQL 16 installation)

### 2. Environment Setup

```powershell
# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

### 3. Start PostgreSQL Database

Start the PostgreSQL 16 container via Docker Compose:
```powershell
docker compose up -d db
```

Verify the container is healthy:
```powershell
docker ps
```

### 4. Run Database Migrations

Apply Alembic migrations to create the schema, `employees`, and `sessions` tables:
```powershell
alembic upgrade head
```

### 5. Provision Administrator Account (CLI)

Safely create the initial administrator without exposing a public endpoint or echoing passwords:
```powershell
python -m app.cli create-admin
```

To provision employees or HR staff:
```powershell
python -m app.cli create-employee
```

### 6. Run the Development Server
```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8081
```

### 7. Run Automated Tests
```powershell
pytest -v
```

---

## Available Endpoints

### Web Frontend Views (Stage 5)
- **Corporate Login Screen:** `GET /login` [http://127.0.0.1:8081/login](http://127.0.0.1:8081/login) — Secure login with password toggle, inline validation, and rate limit error feedback.
- **Employee Portal Dashboard:** `GET /dashboard` [http://127.0.0.1:8081/dashboard](http://127.0.0.1:8081/dashboard) — Responsive dashboard with dynamic role-specific navigation, directory search/filters, self-service profile updating, provisioning forms, and admin confirmation modals.
- **Convenience Redirect:** `GET /portal` -> Redirects to `/dashboard`.

### General & System
- **Root Welcome:** `GET /` [http://127.0.0.1:8081/](http://127.0.0.1:8081/)
- **Service Health:** `GET /health` [http://127.0.0.1:8081/health](http://127.0.0.1:8081/health)
- **Database Health Check:** `GET /health/db` [http://127.0.0.1:8081/health/db](http://127.0.0.1:8081/health/db)
- **Interactive Swagger Docs:** `GET /docs` [http://127.0.0.1:8081/docs](http://127.0.0.1:8081/docs)
- **ReDoc UI:** `GET /redoc` [http://127.0.0.1:8081/redoc](http://127.0.0.1:8081/redoc)

### Authentication (Stage 3)
- **Employee Login:** `POST /auth/login` (email, password -> sets `session_id` and `csrf_token` cookies, rate limited)
- **Current User Profile:** `GET /auth/me` (reads session cookie, validates against PostgreSQL, returns safe profile fields)
- **Employee Logout:** `POST /auth/logout` (requires `X-CSRF-Token` header, invalidates session in DB, clears cookies)

### Employee Directory & Management (Stage 4 — RBAC)
- **Current Employee Profile:** `GET /employees/me` *(Authenticated: Employee, HR, Admin)*
- **Employee Directory:** `GET /employees` *(HR, Admin only)* — Supports pagination (`skip`, `limit`) and filters (`department`, `role`, `search`).
- **View Employee Record:** `GET /employees/{employee_id}` *(Employee can only view self; HR and Admin can view any).*
- **Update Employee Details:** `PATCH /employees/{employee_id}` *(CSRF protected; Employee can only update own name; HR and Admin can update name and department).*

### Administrative & HR Operations (Stage 4 — RBAC)
- **Update Role:** `PATCH /admin/employees/{employee_id}/role` *(Admin only, CSRF protected)* — Prevents self-demotion.
- **Update Status:** `PATCH /admin/employees/{employee_id}/status` *(Admin only, CSRF protected)* — Deactivation immediately revokes all sessions; prevents self-deactivation.
- **Provision Account:** `POST /hr/employees` *(HR, Admin only, CSRF protected)* — HR cannot create Admin accounts. Never returns passwords or hashes.

---

## RBAC Permissions Matrix

| Resource / Action | Employee | HR | Admin | Notes |
|---|:---:|:---:|:---:|---|
| `GET /employees/me` | Allowed | Allowed | Allowed | Safe profile (no secrets) |
| `GET /employees` (Directory) | Forbidden (403) | Allowed | Allowed | Paginated |
| `GET /employees/{id}` (Self) | Allowed | Allowed | Allowed | Own record only for Employee |
| `GET /employees/{id}` (Others) | Forbidden (403) | Allowed | Allowed | HR/Admin operational lookup |
| `PATCH /employees/{id}` (Self) | Allowed (name only) | Allowed (name, dept) | Allowed (name, dept) | CSRF required |
| `PATCH /employees/{id}` (Others) | Forbidden (403) | Allowed (name, dept) | Allowed (name, dept) | CSRF required |
| `PATCH /admin/employees/{id}/role` | Forbidden (403) | Forbidden (403) | Allowed | Prevents self-demotion |
| `PATCH /admin/employees/{id}/status` | Forbidden (403) | Forbidden (403) | Allowed | Revokes sessions upon deactivation; prevents self-deactivation |
| `POST /hr/employees` (Employee role) | Forbidden (403) | Allowed | Allowed | Password validated and Argon2-hashed |
| `POST /hr/employees` (Admin role) | Forbidden (403) | Forbidden (403) | Allowed | HR forbidden from creating Admins |
