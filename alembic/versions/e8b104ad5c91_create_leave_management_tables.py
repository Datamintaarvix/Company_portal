"""create_leave_management_tables

Revision ID: e8b104ad5c91
Revises: c2c3927fd40e
Create Date: 2026-10-31 11:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8b104ad5c91'
down_revision: Union[str, Sequence[str], None] = 'c2c3927fd40e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: creates 7 Leave Management tables and associated constraints."""

    # 1. leave_categories
    op.create_table(
        'leave_categories',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('code', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=50), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('annual_quota', sa.Numeric(precision=4, scale=1), server_default=sa.text('0.0'), nullable=False),
        sa.Column('is_paid', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('max_carry_forward', sa.Numeric(precision=4, scale=1), server_default=sa.text('0.0'), nullable=False),
        sa.Column('accrual_rate_monthly', sa.Numeric(precision=3, scale=1), server_default=sa.text('0.0'), nullable=False),
        sa.Column('requires_approval', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_leave_categories_code'),
    )
    op.create_index(op.f('ix_leave_categories_code'), 'leave_categories', ['code'], unique=True)

    # 2. company_holidays
    op.create_table(
        'company_holidays',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('holiday_date', sa.Date(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('is_optional', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('holiday_date', name='uq_company_holidays_holiday_date'),
    )
    op.create_index(op.f('ix_company_holidays_holiday_date'), 'company_holidays', ['holiday_date'], unique=True)
    op.create_index(op.f('ix_company_holidays_year'), 'company_holidays', ['year'], unique=False)

    # 3. leave_balances
    op.create_table(
        'leave_balances',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('employee_id', sa.Integer(), nullable=False),
        sa.Column('category_id', sa.Integer(), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('entitled_days', sa.Numeric(precision=4, scale=1), server_default=sa.text('0.0'), nullable=False),
        sa.Column('carried_forward_days', sa.Numeric(precision=4, scale=1), server_default=sa.text('0.0'), nullable=False),
        sa.Column('accrued_days', sa.Numeric(precision=4, scale=1), server_default=sa.text('0.0'), nullable=False),
        sa.Column('used_days', sa.Numeric(precision=4, scale=1), server_default=sa.text('0.0'), nullable=False),
        sa.Column('reserved_days', sa.Numeric(precision=4, scale=1), server_default=sa.text('0.0'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['category_id'], ['leave_categories.id'], ondelete='RESTRICT'),
        sa.UniqueConstraint('employee_id', 'category_id', 'year', name='uq_leave_balance_emp_cat_year'),
        sa.CheckConstraint(
            'entitled_days >= 0 AND carried_forward_days >= 0 AND accrued_days >= 0 AND used_days >= 0 AND reserved_days >= 0',
            name='ck_balance_buckets_non_negative',
        ),
        sa.CheckConstraint(
            '(entitled_days + carried_forward_days + accrued_days) >= (used_days + reserved_days)',
            name='ck_balance_available_non_negative',
        ),
    )
    op.create_index(op.f('ix_leave_balances_employee_id'), 'leave_balances', ['employee_id'], unique=False)
    op.create_index(op.f('ix_leave_balances_category_id'), 'leave_balances', ['category_id'], unique=False)
    op.create_index(op.f('ix_leave_balances_year'), 'leave_balances', ['year'], unique=False)

    # 4. leave_requests
    op.create_table(
        'leave_requests',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('employee_id', sa.Integer(), nullable=False),
        sa.Column('category_id', sa.Integer(), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=False),
        sa.Column('is_half_day', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('half_day_period', sa.String(length=10), nullable=True),
        sa.Column('total_days', sa.Numeric(precision=3, scale=1), nullable=False),
        sa.Column('reason', sa.String(length=500), nullable=False),
        sa.Column('status', sa.String(length=20), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column('reviewed_by_id', sa.Integer(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('review_notes', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['category_id'], ['leave_categories.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['reviewed_by_id'], ['employees.id'], ondelete='SET NULL'),
        sa.CheckConstraint('end_date >= start_date', name='ck_leave_request_dates_order'),
        sa.CheckConstraint('total_days > 0', name='ck_leave_request_total_days_positive'),
        sa.CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED')", name='ck_leave_request_valid_status'),
        sa.CheckConstraint('reviewed_by_id IS NULL OR reviewed_by_id != employee_id', name='ck_leave_request_anti_self_approval'),
        sa.CheckConstraint(
            "(is_half_day = FALSE AND half_day_period IS NULL AND total_days >= 1.0) OR "
            "(is_half_day = TRUE AND half_day_period IN ('morning', 'afternoon') AND total_days = 0.5 AND start_date = end_date)",
            name='ck_leave_request_half_day_shape',
        ),
    )
    op.create_index(op.f('ix_leave_requests_employee_id'), 'leave_requests', ['employee_id'], unique=False)
    op.create_index(op.f('ix_leave_requests_category_id'), 'leave_requests', ['category_id'], unique=False)
    op.create_index(op.f('ix_leave_requests_start_date'), 'leave_requests', ['start_date'], unique=False)
    op.create_index(op.f('ix_leave_requests_end_date'), 'leave_requests', ['end_date'], unique=False)
    op.create_index(op.f('ix_leave_requests_status'), 'leave_requests', ['status'], unique=False)
    op.create_index(op.f('ix_leave_requests_reviewed_by_id'), 'leave_requests', ['reviewed_by_id'], unique=False)
    op.create_index(
        'uq_leave_request_full_day_single',
        'leave_requests',
        ['employee_id', 'start_date'],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'APPROVED') AND is_half_day = FALSE AND start_date = end_date"),
    )
    op.create_index(
        'uq_leave_request_half_day_slot',
        'leave_requests',
        ['employee_id', 'start_date', 'half_day_period'],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'APPROVED') AND is_half_day = TRUE"),
    )

    # 5. comp_off_claims
    op.create_table(
        'comp_off_claims',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('employee_id', sa.Integer(), nullable=False),
        sa.Column('work_date', sa.Date(), nullable=False),
        sa.Column('days_claimed', sa.Numeric(precision=3, scale=1), nullable=False),
        sa.Column('reason', sa.String(length=500), nullable=False),
        sa.Column('status', sa.String(length=20), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column('reviewed_by_id', sa.Integer(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('review_notes', sa.String(length=500), nullable=True),
        sa.Column('days_approved', sa.Numeric(precision=3, scale=1), nullable=True),
        sa.Column('remaining_days', sa.Numeric(precision=3, scale=1), server_default=sa.text('0.0'), nullable=False),
        sa.Column('expiry_date', sa.Date(), nullable=True),
        sa.Column('credit_status', sa.String(length=20), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column('balance_transaction_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['reviewed_by_id'], ['employees.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('balance_transaction_id', name='uq_comp_off_claims_balance_transaction_id'),
        sa.CheckConstraint('days_claimed IN (0.5, 1.0)', name='ck_comp_off_days_claimed'),
        sa.CheckConstraint('days_approved IS NULL OR days_approved IN (0.5, 1.0)', name='ck_comp_off_days_approved'),
        sa.CheckConstraint('remaining_days >= 0', name='ck_comp_off_remaining_non_negative'),
        sa.CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED')", name='ck_comp_off_valid_status'),
        sa.CheckConstraint(
            "credit_status IN ('PENDING', 'ACTIVE', 'PARTIALLY_EXHAUSTED', 'EXHAUSTED', 'EXPIRED')",
            name='ck_comp_off_valid_credit_status',
        ),
        sa.CheckConstraint('reviewed_by_id IS NULL OR reviewed_by_id != employee_id', name='ck_comp_off_anti_self_approval'),
    )
    op.create_index(op.f('ix_comp_off_claims_employee_id'), 'comp_off_claims', ['employee_id'], unique=False)
    op.create_index(op.f('ix_comp_off_claims_work_date'), 'comp_off_claims', ['work_date'], unique=False)
    op.create_index(op.f('ix_comp_off_claims_status'), 'comp_off_claims', ['status'], unique=False)
    op.create_index(op.f('ix_comp_off_claims_reviewed_by_id'), 'comp_off_claims', ['reviewed_by_id'], unique=False)
    op.create_index(op.f('ix_comp_off_claims_expiry_date'), 'comp_off_claims', ['expiry_date'], unique=False)
    op.create_index(op.f('ix_comp_off_claims_credit_status'), 'comp_off_claims', ['credit_status'], unique=False)
    op.create_index(
        'uq_comp_off_claim_emp_date',
        'comp_off_claims',
        ['employee_id', 'work_date'],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'APPROVED')"),
    )

    # 6. leave_balance_transactions
    op.create_table(
        'leave_balance_transactions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('balance_id', sa.Integer(), nullable=False),
        sa.Column('leave_request_id', sa.Integer(), nullable=True),
        sa.Column('comp_off_claim_id', sa.Integer(), nullable=True),
        sa.Column('transaction_type', sa.String(length=30), nullable=False),
        sa.Column('amount', sa.Numeric(precision=4, scale=1), nullable=False),
        sa.Column('accrual_period', sa.String(length=10), nullable=True),
        sa.Column('balance_before', sa.Numeric(precision=4, scale=1), nullable=False),
        sa.Column('balance_after', sa.Numeric(precision=4, scale=1), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('notes', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['balance_id'], ['leave_balances.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['leave_request_id'], ['leave_requests.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['comp_off_claim_id'], ['comp_off_claims.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by_id'], ['employees.id'], ondelete='RESTRICT'),
        sa.CheckConstraint(
            "transaction_type IN ('RESERVE', 'COMMIT', 'RELEASE', 'REFUND', 'ACCRUAL', 'CARRY_FORWARD', 'ADJUSTMENT', 'COMPOFF_CREDIT', 'COMPOFF_EXPIRY')",
            name='ck_tx_valid_type',
        ),
    )
    op.create_index(op.f('ix_leave_balance_transactions_balance_id'), 'leave_balance_transactions', ['balance_id'], unique=False)
    op.create_index(op.f('ix_leave_balance_transactions_leave_request_id'), 'leave_balance_transactions', ['leave_request_id'], unique=False)
    op.create_index(op.f('ix_leave_balance_transactions_comp_off_claim_id'), 'leave_balance_transactions', ['comp_off_claim_id'], unique=False)
    op.create_index(op.f('ix_leave_balance_transactions_transaction_type'), 'leave_balance_transactions', ['transaction_type'], unique=False)
    op.create_index(op.f('ix_leave_balance_transactions_accrual_period'), 'leave_balance_transactions', ['accrual_period'], unique=False)
    op.create_index(op.f('ix_leave_balance_transactions_created_by_id'), 'leave_balance_transactions', ['created_by_id'], unique=False)
    op.create_index(
        'uq_tx_request_type',
        'leave_balance_transactions',
        ['leave_request_id', 'transaction_type'],
        unique=True,
        postgresql_where=sa.text('leave_request_id IS NOT NULL'),
    )
    op.create_index(
        'uq_tx_balance_accrual_period',
        'leave_balance_transactions',
        ['balance_id', 'transaction_type', 'accrual_period'],
        unique=True,
        postgresql_where=sa.text('accrual_period IS NOT NULL'),
    )
    op.create_index(
        'uq_tx_compoff_claim',
        'leave_balance_transactions',
        ['comp_off_claim_id', 'transaction_type'],
        unique=True,
        postgresql_where=sa.text('comp_off_claim_id IS NOT NULL'),
    )

    # Add foreign key from comp_off_claims.balance_transaction_id -> leave_balance_transactions.id
    op.create_foreign_key(
        'fk_comp_off_claims_balance_tx',
        'comp_off_claims',
        'leave_balance_transactions',
        ['balance_transaction_id'],
        ['id'],
        ondelete='SET NULL',
    )

    # 7. leave_audit_logs
    op.create_table(
        'leave_audit_logs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('entity_type', sa.String(length=50), nullable=False),
        sa.Column('entity_id', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(length=50), nullable=False),
        sa.Column('actor_id', sa.Integer(), nullable=False),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('previous_state', sa.Text(), nullable=True),
        sa.Column('new_state', sa.Text(), nullable=True),
        sa.Column('notes', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['actor_id'], ['employees.id'], ondelete='RESTRICT'),
    )
    op.create_index(op.f('ix_leave_audit_logs_entity_type'), 'leave_audit_logs', ['entity_type'], unique=False)
    op.create_index(op.f('ix_leave_audit_logs_entity_id'), 'leave_audit_logs', ['entity_id'], unique=False)
    op.create_index(op.f('ix_leave_audit_logs_actor_id'), 'leave_audit_logs', ['actor_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema: reverses all 7 Leave Management tables and constraints."""

    # 1. Drop foreign key constraint on comp_off_claims.balance_transaction_id
    op.drop_constraint('fk_comp_off_claims_balance_tx', 'comp_off_claims', type_='foreignkey')

    # 2. Drop leave_audit_logs
    op.drop_index(op.f('ix_leave_audit_logs_actor_id'), table_name='leave_audit_logs')
    op.drop_index(op.f('ix_leave_audit_logs_entity_id'), table_name='leave_audit_logs')
    op.drop_index(op.f('ix_leave_audit_logs_entity_type'), table_name='leave_audit_logs')
    op.drop_table('leave_audit_logs')

    # 3. Drop leave_balance_transactions
    op.drop_index('uq_tx_compoff_claim', table_name='leave_balance_transactions', postgresql_where=sa.text('comp_off_claim_id IS NOT NULL'))
    op.drop_index('uq_tx_balance_accrual_period', table_name='leave_balance_transactions', postgresql_where=sa.text('accrual_period IS NOT NULL'))
    op.drop_index('uq_tx_request_type', table_name='leave_balance_transactions', postgresql_where=sa.text('leave_request_id IS NOT NULL'))
    op.drop_index(op.f('ix_leave_balance_transactions_created_by_id'), table_name='leave_balance_transactions')
    op.drop_index(op.f('ix_leave_balance_transactions_accrual_period'), table_name='leave_balance_transactions')
    op.drop_index(op.f('ix_leave_balance_transactions_transaction_type'), table_name='leave_balance_transactions')
    op.drop_index(op.f('ix_leave_balance_transactions_comp_off_claim_id'), table_name='leave_balance_transactions')
    op.drop_index(op.f('ix_leave_balance_transactions_leave_request_id'), table_name='leave_balance_transactions')
    op.drop_index(op.f('ix_leave_balance_transactions_balance_id'), table_name='leave_balance_transactions')
    op.drop_table('leave_balance_transactions')

    # 4. Drop comp_off_claims
    op.drop_index('uq_comp_off_claim_emp_date', table_name='comp_off_claims', postgresql_where=sa.text("status IN ('PENDING', 'APPROVED')"))
    op.drop_index(op.f('ix_comp_off_claims_credit_status'), table_name='comp_off_claims')
    op.drop_index(op.f('ix_comp_off_claims_expiry_date'), table_name='comp_off_claims')
    op.drop_index(op.f('ix_comp_off_claims_reviewed_by_id'), table_name='comp_off_claims')
    op.drop_index(op.f('ix_comp_off_claims_status'), table_name='comp_off_claims')
    op.drop_index(op.f('ix_comp_off_claims_work_date'), table_name='comp_off_claims')
    op.drop_index(op.f('ix_comp_off_claims_employee_id'), table_name='comp_off_claims')
    op.drop_table('comp_off_claims')

    # 5. Drop leave_requests
    op.drop_index('uq_leave_request_half_day_slot', table_name='leave_requests', postgresql_where=sa.text("status IN ('PENDING', 'APPROVED') AND is_half_day = TRUE"))
    op.drop_index('uq_leave_request_full_day_single', table_name='leave_requests', postgresql_where=sa.text("status IN ('PENDING', 'APPROVED') AND is_half_day = FALSE AND start_date = end_date"))
    op.drop_index(op.f('ix_leave_requests_reviewed_by_id'), table_name='leave_requests')
    op.drop_index(op.f('ix_leave_requests_status'), table_name='leave_requests')
    op.drop_index(op.f('ix_leave_requests_end_date'), table_name='leave_requests')
    op.drop_index(op.f('ix_leave_requests_start_date'), table_name='leave_requests')
    op.drop_index(op.f('ix_leave_requests_category_id'), table_name='leave_requests')
    op.drop_index(op.f('ix_leave_requests_employee_id'), table_name='leave_requests')
    op.drop_table('leave_requests')

    # 6. Drop leave_balances
    op.drop_index(op.f('ix_leave_balances_year'), table_name='leave_balances')
    op.drop_index(op.f('ix_leave_balances_category_id'), table_name='leave_balances')
    op.drop_index(op.f('ix_leave_balances_employee_id'), table_name='leave_balances')
    op.drop_table('leave_balances')

    # 7. Drop company_holidays
    op.drop_index(op.f('ix_company_holidays_year'), table_name='company_holidays')
    op.drop_index(op.f('ix_company_holidays_holiday_date'), table_name='company_holidays')
    op.drop_table('company_holidays')

    # 8. Drop leave_categories
    op.drop_index(op.f('ix_leave_categories_code'), table_name='leave_categories')
    op.drop_table('leave_categories')
