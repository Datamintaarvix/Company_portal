"""add date_of_joining to employees

Revision ID: b3f281e7d904
Revises: e8b104ad5c91
Create Date: 2026-11-03 16:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f281e7d904'
down_revision: Union[str, Sequence[str], None] = 'e8b104ad5c91'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('employees', sa.Column('date_of_joining', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('employees', 'date_of_joining')

