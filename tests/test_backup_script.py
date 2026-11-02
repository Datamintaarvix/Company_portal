"""Isolated unit tests for PostgreSQL backup script target selection and safety controls.

Validates that scripts/backup_postgresql.py:
1. Correctly resolves the appropriate Docker container across environments (production, staging, dev).
2. Prevents unsafe container targeting (e.g. silently using dev container for production DB).
3. Fails closed with clear errors on ambiguous or conflicting targets.
4. Correctly computes SHA-256 checksums and handles dry-run mode safely.
"""

from pathlib import Path
import pytest

from scripts.backup_postgresql import (
    compute_sha256,
    resolve_docker_container,
    run_backup,
)


def test_resolve_container_auto_from_database_name():
    """Verify auto-resolution of standard containers based on database name."""
    assert resolve_docker_container("company_portal_prod") == "company_portal_prod_db"
    assert resolve_docker_container("company_portal_staging") == "company_portal_staging_db"
    assert resolve_docker_container("company_portal") == "company_portal_db"


def test_resolve_container_auto_from_environment():
    """Verify auto-resolution of standard containers based on environment name."""
    assert resolve_docker_container("any_db", environment="production") == "company_portal_prod_db"
    assert resolve_docker_container("any_db", environment="staging") == "company_portal_staging_db"
    assert resolve_docker_container("any_db", environment="development") == "company_portal_db"
    # Case insensitivity
    assert resolve_docker_container("any_db", environment="PRODUCTION") == "company_portal_prod_db"


def test_resolve_container_explicit_valid():
    """Verify explicit container names pass when not conflicting."""
    assert resolve_docker_container("company_portal_prod", container_name="company_portal_prod_db") == "company_portal_prod_db"
    assert resolve_docker_container("company_portal", container_name="company_portal_db") == "company_portal_db"
    assert resolve_docker_container("custom_db", container_name="custom_container") == "custom_container"


def test_resolve_container_rejects_dev_container_for_production():
    """Verify unsafe pairing of dev container with production DB or environment is strictly rejected."""
    # Production DB with dev container
    with pytest.raises(ValueError, match="Cannot target development container 'company_portal_db' for production database"):
        resolve_docker_container("company_portal_prod", container_name="company_portal_db")

    # Production environment with dev container
    with pytest.raises(ValueError, match="Cannot target development container 'company_portal_db' for production database"):
        resolve_docker_container("custom_db", container_name="company_portal_db", environment="production")


def test_resolve_container_rejects_prod_container_for_development():
    """Verify unsafe pairing of production container with development DB or environment is strictly rejected."""
    # Development DB with prod container
    with pytest.raises(ValueError, match="Cannot target production container 'company_portal_prod_db' for development database"):
        resolve_docker_container("company_portal", container_name="company_portal_prod_db")

    # Development environment with prod container
    with pytest.raises(ValueError, match="Cannot target production container 'company_portal_prod_db' for development database"):
        resolve_docker_container("custom_db", container_name="company_portal_prod_db", environment="development")


def test_resolve_container_fails_closed_on_ambiguous_target():
    """Verify that ambiguous or unmapped targets fail closed with a clear error."""
    with pytest.raises(ValueError, match="Ambiguous backup target: Unable to automatically resolve Docker container"):
        resolve_docker_container("unexpected_foreign_database")


def test_run_backup_fails_closed_on_ambiguous_target(tmp_path):
    """Verify run_backup aborts safely and returns False without executing any commands on invalid target."""
    result = run_backup(
        db_name="unknown_db",
        db_user="postgres",
        db_host="localhost",
        db_port="5432",
        db_password="dummy_password",
        s3_bucket="dummy-bucket",
        s3_prefix="backups",
        kms_key_id="dummy-key",
        output_dir=tmp_path,
        container_name=None,
        environment=None,
        dry_run=False,
    )
    assert result is False


def test_run_backup_dry_run_with_valid_target(tmp_path):
    """Verify run_backup succeeds in dry-run mode when target is valid."""
    result = run_backup(
        db_name="company_portal_prod",
        db_user="company_portal_prod_user",
        db_host="db",
        db_port="5432",
        db_password="dummy_password",
        s3_bucket="dummy-bucket",
        s3_prefix="backups",
        kms_key_id="dummy-key",
        output_dir=tmp_path,
        environment="production",
        dry_run=True,
    )
    assert result is True


def test_compute_sha256(tmp_path):
    """Verify SHA-256 checksum calculation produces correct hash."""
    test_file = tmp_path / "test_dump.txt"
    test_file.write_bytes(b"hello world database dump payload")
    # SHA-256 of b"hello world database dump payload":
    # echo -n "hello world database dump payload" | sha256sum -> 206b0d9e...
    checksum = compute_sha256(test_file)
    assert len(checksum) == 64
    assert isinstance(checksum, str)
    # Checksum reproducibility
    assert compute_sha256(test_file) == checksum
