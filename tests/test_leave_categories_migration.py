"""Tests for standard leave categories migration and seeding."""

import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.db.base import Base
from app.models.employee import Employee, UserRole
from app.models.leave import LeaveCategory, LeaveCategoryCode, LeaveRequest, LeaveRequestStatus
from app.services.leave import (
    get_employee_balances,
    get_or_create_leave_balance,
    submit_leave_request,
)


def get_migration_module():
    """Load the migration module directly from file path."""
    migration_path = Path("alembic/versions/6a39c5b1b386_seed_leave_categories.py").resolve()
    spec = importlib.util.spec_from_file_location("seed_leave_categories", migration_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sqlite_db():
    """Create an isolated in-memory SQLite database with schema but NO leave categories."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = session_factory()

    yield engine, db

    db.close()
    Base.metadata.drop_all(bind=engine)


def run_migration_upgrade(engine):
    """Execute the upgrade() of the seed migration using Alembic Operations context."""
    migration_mod = get_migration_module()
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.upgrade()
        conn.commit()


def run_migration_downgrade(engine):
    """Execute the downgrade() of the seed migration using Alembic Operations context."""
    migration_mod = get_migration_module()
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.downgrade()
        conn.commit()


def test_migration_seeds_all_six_categories(sqlite_db):
    """Verify upgrade() inserts exactly the 6 standard leave categories with correct attributes."""
    engine, db = sqlite_db

    # Initially empty
    initial_cats = db.execute(select(LeaveCategory)).scalars().all()
    assert len(initial_cats) == 0

    # Run migration upgrade
    run_migration_upgrade(engine)

    # Verify 6 categories exist
    cats = db.execute(select(LeaveCategory).order_by(LeaveCategory.id)).scalars().all()
    assert len(cats) == 6

    cat_map = {c.code: c for c in cats}
    expected_codes = {"CASUAL", "SICK", "EARNED", "UNPAID", "COMP_OFF", "SABBATICAL"}
    assert set(cat_map.keys()) == expected_codes

    # CASUAL
    casual = cat_map["CASUAL"]
    assert casual.name == "Casual Leave"
    assert casual.annual_quota == Decimal("6.0")
    assert casual.is_paid is True
    assert casual.max_carry_forward == Decimal("0.0")
    assert casual.accrual_rate_monthly == Decimal("0.0")
    assert casual.requires_approval is True
    assert casual.is_active is True

    # SICK
    sick = cat_map["SICK"]
    assert sick.name == "Sick Leave"
    assert sick.annual_quota == Decimal("6.0")
    assert sick.is_paid is True
    assert sick.max_carry_forward == Decimal("0.0")
    assert sick.is_active is True

    # EARNED
    earned = cat_map["EARNED"]
    assert earned.name == "Earned Leave"
    assert earned.annual_quota == Decimal("12.0")
    assert earned.is_paid is True
    assert earned.max_carry_forward == Decimal("12.0")
    assert earned.accrual_rate_monthly == Decimal("1.0")
    assert earned.is_active is True

    # UNPAID
    unpaid = cat_map["UNPAID"]
    assert unpaid.name == "Loss of Pay"
    assert unpaid.annual_quota == Decimal("0.0")
    assert unpaid.is_paid is False
    assert unpaid.is_active is True

    # COMP_OFF
    compoff = cat_map["COMP_OFF"]
    assert compoff.name == "Compensatory Off"
    assert compoff.annual_quota == Decimal("0.0")
    assert compoff.is_paid is True
    assert compoff.is_active is True

    # SABBATICAL
    sabbatical = cat_map["SABBATICAL"]
    assert sabbatical.name == "Sabbatical Leave"
    assert sabbatical.annual_quota == Decimal("0.0")
    assert sabbatical.is_paid is False
    assert sabbatical.is_active is True


def test_migration_idempotent_no_duplicates(sqlite_db):
    """Verify running upgrade() repeatedly does not create duplicate categories."""
    engine, db = sqlite_db

    run_migration_upgrade(engine)
    cats_run1 = db.execute(select(LeaveCategory)).scalars().all()
    assert len(cats_run1) == 6

    # Run upgrade a second time
    run_migration_upgrade(engine)
    cats_run2 = db.execute(select(LeaveCategory)).scalars().all()
    assert len(cats_run2) == 6


def test_migration_preserves_preexisting_category(sqlite_db):
    """Verify upgrade() only inserts missing categories if some already exist."""
    engine, db = sqlite_db

    # Pre-insert CASUAL with a custom description
    pre_casual = LeaveCategory(
        code="CASUAL",
        name="Existing Casual Leave",
        description="Pre-existing custom description",
        annual_quota=Decimal("6.0"),
        is_paid=True,
    )
    db.add(pre_casual)
    db.commit()

    assert len(db.execute(select(LeaveCategory)).scalars().all()) == 1

    # Run migration upgrade
    run_migration_upgrade(engine)

    cats = db.execute(select(LeaveCategory)).scalars().all()
    assert len(cats) == 6

    # Verify the pre-existing CASUAL was NOT overwritten or duplicated
    casual = db.execute(select(LeaveCategory).where(LeaveCategory.code == "CASUAL")).scalar_one()
    assert casual.name == "Existing Casual Leave"
    assert casual.description == "Pre-existing custom description"


def test_migration_downgrade(sqlite_db):
    """Verify downgrade() removes the standard categories cleanly."""
    engine, db = sqlite_db

    # Seed via upgrade
    run_migration_upgrade(engine)
    assert len(db.execute(select(LeaveCategory)).scalars().all()) == 6

    # Downgrade
    run_migration_downgrade(engine)
    assert len(db.execute(select(LeaveCategory)).scalars().all()) == 0


def test_leave_balances_self_healing_after_migration(sqlite_db):
    """Verify get_employee_balances() self-heals and generates balances after migration."""
    engine, db = sqlite_db

    run_migration_upgrade(engine)

    emp = Employee(
        employee_code="EMP001",
        name="Test Employee",
        email="test@company.com",
        role=UserRole.EMPLOYEE.value,
        is_active=True,
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)

    # Calling get_employee_balances auto-populates balances
    balances = get_employee_balances(db=db, employee_id=emp.id, year=2026)
    assert len(balances) == 6

    bal_map = {b.category.code: b for b in balances}
    assert bal_map["CASUAL"].entitled_days == Decimal("6.0")
    assert bal_map["SICK"].entitled_days == Decimal("6.0")
    assert bal_map["EARNED"].entitled_days == Decimal("12.0")
    assert bal_map["UNPAID"].entitled_days == Decimal("0.0")


def test_leave_request_submission_succeeds_after_migration(sqlite_db):
    """Verify that submit_leave_request succeeds without 'category does not exist' error."""
    engine, db = sqlite_db

    run_migration_upgrade(engine)

    emp = Employee(
        employee_code="EMP002",
        name="Jane Doe",
        email="jane@company.com",
        role=UserRole.EMPLOYEE.value,
        is_active=True,
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)

    # Submit a Casual Leave request
    response = submit_leave_request(
        db=db,
        employee_id=emp.id,
        category_code="CASUAL",
        start_date=date(2026, 12, 1),
        end_date=date(2026, 12, 1),
        is_half_day=False,
        half_day_period=None,
        reason="Family event",
    )

    assert response.status == LeaveRequestStatus.PENDING.value
    assert response.category_code == "CASUAL"
    assert response.total_days == Decimal("1.0")

    # Verify balance was reserved
    balances = get_employee_balances(db=db, employee_id=emp.id, year=2026)
    bal_casual = [b for b in balances if b.category.code == "CASUAL"][0]
    assert bal_casual.reserved_days == Decimal("1.0")
    assert bal_casual.available_days == Decimal("5.0")
