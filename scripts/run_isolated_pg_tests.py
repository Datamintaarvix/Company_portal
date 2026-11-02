"""Isolated PostgreSQL Test Runner and Harness for Leave Management Concurrency.

Phase 2.11A: Isolated PostgreSQL Integration and Concurrency Testing.

SAFETY CONSTRAINTS:
1. Mandatory confirmation flag: Refuses execution unless --confirm-isolated-execution
   or CONFIRM_ISOLATED_PG_EXECUTION=true is explicitly set.
2. Zero application default connection: Never defaults to or reads application
   development, staging, or production database URLs.
3. Fail-closed target validation: Aborts immediately if admin or ephemeral target
   matches any protected database (company_portal, company_portal_dev,
   company_portal_staging, company_portal_prod, postgres, template1).
4. Ephemeral naming: Mandates strict pattern `company_portal_test_ephemeral_<uuid8>`.
5. Cryptographic ownership stamping: Records a unique run token in a PostgreSQL
   database COMMENT and refuses to drop the database if the token does not match.
6. Safe connection termination: Closes active backends via `pg_terminate_backend`
   prior to dropping the ephemeral database in the `finally` block.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import logging
import os
from pathlib import Path
import re
import sys
from typing import Generator, Optional
from urllib.parse import urlparse, urlunparse
import uuid

# Ensure the project root is importable when this script is run directly.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import sqlalchemy as sa
from sqlalchemy.engine import Engine, create_engine
from sqlalchemy.pool import NullPool

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("company_portal.test_harness_pg")

# Protected database names that must NEVER be targeted, altered, migrated, or dropped
PROTECTED_DATABASES: frozenset[str] = frozenset({
    "company_portal",
    "company_portal_dev",
    "company_portal_staging",
    "company_portal_prod",
    "postgres",
    "template1",
})

# Ephemeral database naming pattern: company_portal_test_ephemeral_<8 hex chars>
EPHEMERAL_DB_PATTERN: re.Pattern = re.compile(
    r"^company_portal_test_ephemeral_[0-9a-f]{8}$"
)

# Marker prefix for database comment ownership
OWNERSHIP_COMMENT_PREFIX = "ephemeral_test_harness_run:"


class SecurityViolationError(Exception):
    """Raised when a security rule, naming invariant, or fail-closed guard is violated."""
    pass


class OwnershipVerificationError(Exception):
    """Raised when the database ownership token cannot be verified prior to cleanup."""
    pass


def validate_admin_connection_target(admin_url_str: str) -> None:
    """Validate that the admin connection string does NOT target any application database.

    Fail-closed checks:
    - Must be a valid PostgreSQL URL.
    - Path (target database) must be a maintenance database (e.g. 'postgres' or 'template1'),
      NEVER an application database (company_portal, company_portal_prod, etc.).
    """
    if not admin_url_str or not admin_url_str.strip():
        raise SecurityViolationError(
            "Admin database connection URL is empty. Must be provided explicitly via "
            "--admin-url or EPHEMERAL_PG_ADMIN_URL."
        )

    parsed = urlparse(admin_url_str)
    if parsed.scheme not in ("postgresql", "postgres", "postgresql+psycopg", "postgresql+psycopg2"):
        raise SecurityViolationError(
            f"Invalid database scheme '{parsed.scheme}'. Only PostgreSQL connections are permitted."
        )

    target_db = parsed.path.lstrip("/")
    if not target_db:
        raise SecurityViolationError(
            "Admin database connection URL must explicitly specify a maintenance database (e.g. '/postgres')."
        )

    # Admin URL can connect to postgres or template1 to issue CREATE/DROP DATABASE,
    # but must NEVER target an application database
    if target_db.lower() not in ("postgres", "template1"):
        raise SecurityViolationError(
            f"SECURITY ABORT: Admin connection URL targets '{target_db}'. "
            "Only maintenance databases 'postgres' or 'template1' are permitted."
        )


def validate_ephemeral_db_name(db_name: str) -> None:
    """Strictly validate ephemeral database name against fail-closed rules."""
    if not db_name or not db_name.strip():
        raise SecurityViolationError("Database name cannot be empty.")

    cleaned_name = db_name.strip().lower()

    # Fail closed on protected names
    if cleaned_name in PROTECTED_DATABASES:
        raise SecurityViolationError(
            f"SECURITY ABORT: Database name '{cleaned_name}' is in the protected database list!"
        )

    # Protect against accidental substrings
    for protected in ("company_portal_prod", "company_portal_staging", "company_portal_dev"):
        if protected in cleaned_name:
            raise SecurityViolationError(
                f"SECURITY ABORT: Database name '{cleaned_name}' contains protected keyword '{protected}'!"
            )

    # Must match strict ephemeral regex
    if not EPHEMERAL_DB_PATTERN.match(cleaned_name):
        raise SecurityViolationError(
            f"SECURITY ABORT: Database name '{cleaned_name}' does not match required ephemeral pattern "
            f"'company_portal_test_ephemeral_<8 hex chars>'."
        )


def build_ephemeral_database_url(admin_url_str: str, ephemeral_db_name: str) -> str:
    """Construct the connection URL for the newly created ephemeral database."""
    parsed = urlparse(admin_url_str)
    # Ensure psycopg driver is specified if generic postgresql
    scheme = parsed.scheme
    if scheme in ("postgresql", "postgres"):
        scheme = "postgresql+psycopg"

    new_parts = parsed._replace(scheme=scheme, path=f"/{ephemeral_db_name}")
    return urlunparse(new_parts)


class EphemeralPostgresHarness:
    """Orchestrates creation, ownership tagging, test execution, and cleanup of an ephemeral DB."""

    def __init__(
        self,
        admin_url: str,
        confirm_execution: bool = False,
        keep_db_on_failure: bool = False,
    ) -> None:
        self.admin_url = admin_url.strip()
        self.confirm_execution = confirm_execution
        self.keep_db_on_failure = keep_db_on_failure

        # Unique run token and database name generated per instance
        self.run_token = uuid.uuid4().hex
        self.ephemeral_db_name = f"company_portal_test_ephemeral_{self.run_token[:8]}"
        self.ephemeral_db_url: Optional[str] = None
        self._admin_engine: Optional[Engine] = None
        self._db_created = False

    def verify_preconditions(self) -> None:
        """Verify all execution prerequisites and fail closed if any are violated."""
        if not self.confirm_execution:
            raise SecurityViolationError(
                "Execution refused: Mandatory confirmation flag is missing.\n"
                "To run isolated tests, supply --confirm-isolated-execution or set "
                "CONFIRM_ISOLATED_PG_EXECUTION=true."
            )

        validate_admin_connection_target(self.admin_url)
        validate_ephemeral_db_name(self.ephemeral_db_name)

    def _get_admin_engine(self) -> Engine:
        """Create a dedicated engine for maintenance commands with AUTOCOMMIT isolation."""
        if self._admin_engine is None:
            # Normalize scheme to postgresql+psycopg if generic postgresql/postgres to use Psycopg 3
            parsed = urlparse(self.admin_url)
            scheme = "postgresql+psycopg" if parsed.scheme in ("postgresql", "postgres") else parsed.scheme
            admin_url = urlunparse(parsed._replace(scheme=scheme))

            # Psycopg3 engine with NullPool and autocommit execution
            self._admin_engine = create_engine(
                admin_url,
                poolclass=NullPool,
                isolation_level="AUTOCOMMIT",
            )
        return self._admin_engine

    def setup_database(self) -> str:
        """Create the ephemeral database and stamp it with the cryptographic run token."""
        self.verify_preconditions()
        logger.info(
            "Creating ephemeral test database '%s' with run token '%s'...",
            self.ephemeral_db_name,
            self.run_token,
        )

        admin_engine = self._get_admin_engine()
        with admin_engine.connect() as conn:
            # 1. Create ephemeral database
            create_sql = sa.text(f'CREATE DATABASE "{self.ephemeral_db_name}" TEMPLATE template1;')
            conn.execute(create_sql)
            self._db_created = True

            # 2. Record ownership marker via PostgreSQL database COMMENT
            # PostgreSQL DDL does not support prepared bind parameters in COMMENT ON statements.
            # We strictly validate the token format and database name, bind the comment value,
            # and safely compile with dialect-specific literal_binds rendering to eliminate SQL injection risk.
            if not EPHEMERAL_DB_PATTERN.match(self.ephemeral_db_name):
                raise SecurityViolationError(
                    f"SECURITY ABORT: Database name '{self.ephemeral_db_name}' failed pattern validation."
                )
            if not re.match(r"^[0-9a-f]{32}$", self.run_token):
                raise SecurityViolationError("Invalid cryptographic run token format.")

            ownership_comment = f"{OWNERSHIP_COMMENT_PREFIX}{self.run_token}"
            comment_stmt = sa.text(
                f'COMMENT ON DATABASE "{self.ephemeral_db_name}" IS :comment'
            ).bindparams(sa.bindparam("comment", ownership_comment, sa.String()))
            compiled_comment_sql = comment_stmt.compile(
                dialect=conn.dialect,
                compile_kwargs={"literal_binds": True},
            )
            conn.execute(sa.text(str(compiled_comment_sql)))

        self.ephemeral_db_url = build_ephemeral_database_url(
            self.admin_url, self.ephemeral_db_name
        )
        logger.info("Ephemeral test database successfully created and verified.")
        return self.ephemeral_db_url

    def verify_database_ownership(self, conn: sa.Connection) -> bool:
        """Verify the database exists and bears this run's exact ownership token."""
        query = sa.text("""
            SELECT d.description
            FROM pg_shdescription d
            JOIN pg_database b ON b.oid = d.objoid
            WHERE b.datname = :dbname
        """)
        result = conn.execute(query, {"dbname": self.ephemeral_db_name}).scalar_one_or_none()
        expected = f"{OWNERSHIP_COMMENT_PREFIX}{self.run_token}"
        return result == expected

    def teardown_database(self, test_failed: bool = False) -> None:
        """Safely terminate connections and drop the ephemeral database."""
        if not self._db_created:
            logger.info("No ephemeral database was created; teardown skipped.")
            return

        if test_failed and self.keep_db_on_failure:
            logger.warning(
                "PRESERVING ephemeral database '%s' for debugging due to --keep-db-on-failure.",
                self.ephemeral_db_name,
            )
            return

        # Double check naming invariants before attempting teardown
        validate_ephemeral_db_name(self.ephemeral_db_name)

        admin_engine = self._get_admin_engine()
        with admin_engine.connect() as conn:
            # 1. Verify ownership marker
            if not self.verify_database_ownership(conn):
                raise OwnershipVerificationError(
                    f"CRITICAL SAFETY REFUSAL: Database '{self.ephemeral_db_name}' failed "
                    f"ownership token verification! Expected run token '{self.run_token}'. "
                    f"Refusing to drop database."
                )

            # 2. Terminate all active backend connections to the ephemeral database
            logger.info("Terminating active connections to '%s'...", self.ephemeral_db_name)
            terminate_sql = sa.text("""
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = :dbname AND pid <> pg_backend_pid();
            """)
            conn.execute(terminate_sql, {"dbname": self.ephemeral_db_name})

            # 3. Drop ephemeral database with FORCE
            logger.info("Dropping ephemeral database '%s'...", self.ephemeral_db_name)
            drop_sql = sa.text(f'DROP DATABASE IF EXISTS "{self.ephemeral_db_name}" WITH (FORCE);')
            conn.execute(drop_sql)

        self._db_created = False
        logger.info("Ephemeral database '%s' dropped successfully.", self.ephemeral_db_name)

    @contextmanager
    def lifecycle(self) -> Generator[str, None, None]:
        """Context manager encapsulating the ephemeral database lifecycle."""
        ephemeral_url = ""
        test_failed = False
        try:
            ephemeral_url = self.setup_database()
            yield ephemeral_url
        except Exception:
            test_failed = True
            raise
        finally:
            if self._admin_engine:
                try:
                    self.teardown_database(test_failed=test_failed)
                except Exception as cleanup_err:
                    logger.error("Failed to teardown ephemeral database: %s", cleanup_err)
                finally:
                    self._admin_engine.dispose()


def run_tests(
    admin_url: str,
    confirm: bool,
    test_target: str = "tests/test_leave_concurrency_pg.py",
    keep_db_on_failure: bool = False,
    extra_pytest_args: Optional[list[str]] = None,
) -> int:
    """Execute pytest against the ephemeral database within the safety harness."""
    harness = EphemeralPostgresHarness(
        admin_url=admin_url,
        confirm_execution=confirm,
        keep_db_on_failure=keep_db_on_failure,
    )

    logger.info("=" * 70)
    logger.info("Phase 2.11A: Isolated PostgreSQL Test Harness Initialized")
    logger.info("=" * 70)

    with harness.lifecycle() as ephemeral_db_url:
        import pytest

        # Set environment variables consumed by test_leave_concurrency_pg.py
        env_backup = {
            "EPHEMERAL_PG_TEST_DB_URL": os.environ.get("EPHEMERAL_PG_TEST_DB_URL"),
            "EPHEMERAL_PG_RUN_TOKEN": os.environ.get("EPHEMERAL_PG_RUN_TOKEN"),
            "DATABASE_URL": os.environ.get("DATABASE_URL"),
        }

        get_settings = None

        os.environ["EPHEMERAL_PG_TEST_DB_URL"] = ephemeral_db_url
        os.environ["EPHEMERAL_PG_RUN_TOKEN"] = harness.run_token
        os.environ["DATABASE_URL"] = ephemeral_db_url

        try:
            # Clear settings cache so any app code or alembic reads the ephemeral URL
            from app.core.config import get_settings as app_get_settings
            get_settings = app_get_settings
            get_settings.cache_clear()

            # Build pytest command arguments
            pytest_args = [test_target, "-v", "-s"]
            if extra_pytest_args:
                pytest_args.extend(extra_pytest_args)

            logger.info("Invoking pytest on '%s'...", test_target)
            exit_code = pytest.main(pytest_args)
            return int(exit_code)
        finally:
            # Restore environment variables
            for k, v in env_backup.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            # Clear settings cache again
            if get_settings is not None:
                get_settings.cache_clear()


def build_cli_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser with explicit flags."""
    parser = argparse.ArgumentParser(
        description="Isolated PostgreSQL Concurrency Test Runner (Phase 2.11A)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--admin-url",
        type=str,
        default=os.environ.get("EPHEMERAL_PG_ADMIN_URL", ""),
        help="PostgreSQL maintenance connection URL (e.g. postgresql://user:pass@localhost:5432/postgres). "
             "Defaults to EPHEMERAL_PG_ADMIN_URL env var.",
    )
    parser.add_argument(
        "--confirm-isolated-execution",
        action="store_true",
        default=os.environ.get("CONFIRM_ISOLATED_PG_EXECUTION", "").lower() in ("true", "1", "yes"),
        help="Mandatory confirmation flag required to execute tests.",
    )
    parser.add_argument(
        "--test-target",
        type=str,
        default="tests/test_leave_concurrency_pg.py",
        help="Pytest test file or directory target.",
    )
    parser.add_argument(
        "--keep-db-on-failure",
        action="store_true",
        default=False,
        help="Keep ephemeral database intact if tests fail (for forensic inspection).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate parameters and display planned execution without connecting to PostgreSQL.",
    )
    return parser


def main() -> int:
    """CLI entrypoint."""
    parser = build_cli_parser()
    args, unknown_args = parser.parse_known_args()

    if args.dry_run:
        print("\n--- DRY RUN: Isolated PostgreSQL Test Harness ---")
        print(f"Target test: {args.test_target}")
        print(f"Admin URL provided: {'[YES]' if args.admin_url else '[NO - REQUIRED]'}")
        print(f"Confirmed: {args.confirm_isolated_execution}")
        print(f"Keep DB on failure: {args.keep_db_on_failure}")
        try:
            validate_admin_connection_target(args.admin_url)
            print("Admin URL validation: PASS (targets maintenance database)")
        except Exception as e:
            print(f"Admin URL validation: FAIL ({e})")
        return 0

    if not args.admin_url:
        logger.error(
            "Missing admin connection URL. Supply --admin-url or set EPHEMERAL_PG_ADMIN_URL."
        )
        return 1

    if not args.confirm_isolated_execution:
        logger.error(
            "Confirmation flag missing. Execution refused. Supply --confirm-isolated-execution "
            "or set CONFIRM_ISOLATED_PG_EXECUTION=true."
        )
        return 1

    try:
        return run_tests(
            admin_url=args.admin_url,
            confirm=args.confirm_isolated_execution,
            test_target=args.test_target,
            keep_db_on_failure=args.keep_db_on_failure,
            extra_pytest_args=unknown_args,
        )
    except SecurityViolationError as sec_err:
        logger.error("SECURITY VIOLATION: %s", sec_err)
        return 2
    except OwnershipVerificationError as own_err:
        logger.error("OWNERSHIP VERIFICATION FAILURE: %s", own_err)
        return 3
    except Exception as exc:
        logger.exception("Unexpected harness error: %s", exc)
        return 4


if __name__ == "__main__":
    sys.exit(main())
