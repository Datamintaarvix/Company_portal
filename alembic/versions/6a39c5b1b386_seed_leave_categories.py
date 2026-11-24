"""seed standard leave categories

Revision ID: 6a39c5b1b386
Revises: b3f281e7d904
Create Date: 2026-11-24 10:00:00.000000

"""
from decimal import Decimal
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6a39c5b1b386'
down_revision: Union[str, Sequence[str], None] = 'b3f281e7d904'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STANDARD_CATEGORIES = [
    {
        "code": "CASUAL",
        "name": "Casual Leave",
        "description": "Standard paid casual leave for personal reasons",
        "annual_quota": Decimal("6.0"),
        "is_paid": True,
        "max_carry_forward": Decimal("0.0"),
        "accrual_rate_monthly": Decimal("0.0"),
        "requires_approval": True,
        "is_active": True,
    },
    {
        "code": "SICK",
        "name": "Sick Leave",
        "description": "Paid leave for illness or medical appointments",
        "annual_quota": Decimal("6.0"),
        "is_paid": True,
        "max_carry_forward": Decimal("0.0"),
        "accrual_rate_monthly": Decimal("0.0"),
        "requires_approval": True,
        "is_active": True,
    },
    {
        "code": "EARNED",
        "name": "Earned Leave",
        "description": "Accrued annual privilege leave with carry-forward",
        "annual_quota": Decimal("12.0"),
        "is_paid": True,
        "max_carry_forward": Decimal("12.0"),
        "accrual_rate_monthly": Decimal("1.0"),
        "requires_approval": True,
        "is_active": True,
    },
    {
        "code": "UNPAID",
        "name": "Loss of Pay",
        "description": "Unpaid absence with loss of pay",
        "annual_quota": Decimal("0.0"),
        "is_paid": False,
        "max_carry_forward": Decimal("0.0"),
        "accrual_rate_monthly": Decimal("0.0"),
        "requires_approval": True,
        "is_active": True,
    },
    {
        "code": "COMP_OFF",
        "name": "Compensatory Off",
        "description": "Compensatory off earned from approved non-working day shifts",
        "annual_quota": Decimal("0.0"),
        "is_paid": True,
        "max_carry_forward": Decimal("0.0"),
        "accrual_rate_monthly": Decimal("0.0"),
        "requires_approval": True,
        "is_active": True,
    },
    {
        "code": "SABBATICAL",
        "name": "Sabbatical Leave",
        "description": "Extended unpaid leave for education, research, or personal pursuit",
        "annual_quota": Decimal("0.0"),
        "is_paid": False,
        "max_carry_forward": Decimal("0.0"),
        "accrual_rate_monthly": Decimal("0.0"),
        "requires_approval": True,
        "is_active": True,
    },
]


def upgrade() -> None:
    """Idempotently seed the 6 standard leave categories into leave_categories."""
    bind = op.get_bind()

    # Query already existing category codes to guarantee idempotency
    existing_codes = set()
    result = bind.execute(sa.text("SELECT code FROM leave_categories"))
    for row in result:
        existing_codes.add(row[0])

    categories_to_insert = [
        cat for cat in STANDARD_CATEGORIES if cat["code"] not in existing_codes
    ]

    if categories_to_insert:
        leave_categories_table = sa.table(
            "leave_categories",
            sa.column("code", sa.String),
            sa.column("name", sa.String),
            sa.column("description", sa.String),
            sa.column("annual_quota", sa.Numeric),
            sa.column("is_paid", sa.Boolean),
            sa.column("max_carry_forward", sa.Numeric),
            sa.column("accrual_rate_monthly", sa.Numeric),
            sa.column("requires_approval", sa.Boolean),
            sa.column("is_active", sa.Boolean),
        )
        op.bulk_insert(leave_categories_table, categories_to_insert)


def downgrade() -> None:
    """Remove the standard leave categories introduced by this migration."""
    op.execute(
        sa.text(
            "DELETE FROM leave_categories "
            "WHERE code IN ('CASUAL', 'SICK', 'EARNED', 'UNPAID', 'COMP_OFF', 'SABBATICAL')"
        )
    )
