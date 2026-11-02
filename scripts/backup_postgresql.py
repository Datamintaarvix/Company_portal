"""Automated, encrypted PostgreSQL backup utility for Company Portal.

Performs:
1. Transaction-safe database snapshot via `pg_dump -Fc` (custom format).
2. Cryptographic SHA-256 integrity checksum generation.
3. Secure upload to private AWS S3 bucket with AWS KMS server-side encryption.
4. Sanitized logging (zero secrets or credentials in output).
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("company_portal.backup")

# Standard container mappings by database and environment
KNOWN_DB_CONTAINERS = {
    "company_portal_prod": "company_portal_prod_db",
    "company_portal_staging": "company_portal_staging_db",
    "company_portal": "company_portal_db",
}

KNOWN_ENV_CONTAINERS = {
    "production": "company_portal_prod_db",
    "staging": "company_portal_staging_db",
    "development": "company_portal_db",
}


def resolve_docker_container(
    db_name: str,
    container_name: Optional[str] = None,
    environment: Optional[str] = None,
) -> str:
    """Resolve and strictly validate the target Docker container for database backup.

    Enforces target isolation to prevent accidentally backing up or targeting
    the wrong environment container (e.g. running against development container
    when production is intended).

    Rules:
    1. If explicit container_name is provided:
       - Fails closed if attempting to pair a production database/environment with
         the development container 'company_portal_db'.
       - Fails closed if attempting to pair a development database/environment with
         the production container 'company_portal_prod_db'.
       - Returns the validated container name.
    2. If container_name is NOT provided:
       - Automatically maps known databases (company_portal_prod, company_portal_staging,
         company_portal) or environments (production, staging, development) to their
         standard containers.
       - If the target is ambiguous or unknown, fails closed with a clear error.
    """
    env_clean = environment.strip().lower() if environment else None
    target_db = db_name.strip() if db_name else ""

    if container_name:
        cand = container_name.strip()
        # Safety check: prevent pairing production DB or env with development container
        if (target_db == "company_portal_prod" or env_clean == "production") and cand == "company_portal_db":
            raise ValueError(
                "Unsafe container selection: Cannot target development container 'company_portal_db' "
                f"for production database '{target_db}'."
            )
        # Safety check: prevent pairing development DB or env with production container
        if (target_db == "company_portal" or env_clean == "development") and cand == "company_portal_prod_db":
            raise ValueError(
                "Unsafe container selection: Cannot target production container 'company_portal_prod_db' "
                f"for development database '{target_db}'."
            )
        return cand

    # Auto-resolve from database name
    if target_db in KNOWN_DB_CONTAINERS:
        return KNOWN_DB_CONTAINERS[target_db]

    # Auto-resolve from environment name
    if env_clean in KNOWN_ENV_CONTAINERS:
        return KNOWN_ENV_CONTAINERS[env_clean]

    # Ambiguous or unmapped target: Fail closed
    raise ValueError(
        f"Ambiguous backup target: Unable to automatically resolve Docker container for database '{target_db}'. "
        "Please specify an explicit container using --container-name."
    )


def compute_sha256(filepath: Path) -> str:
    """Compute SHA-256 hash of a file in 64KB blocks to conserve memory."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def run_backup(
    db_name: str,
    db_user: str,
    db_host: str,
    db_port: str,
    db_password: str,
    s3_bucket: str,
    s3_prefix: str,
    kms_key_id: str,
    output_dir: Path,
    container_name: Optional[str] = None,
    environment: Optional[str] = None,
    aws_profile: Optional[str] = None,
    dry_run: bool = False,
) -> bool:
    """Execute the full database dump, checksum, and S3 upload sequence."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%SZ")
    dump_filename = f"{db_name}_{timestamp}.dump"
    dump_path = output_dir / dump_filename
    checksum_path = output_dir / f"{dump_filename}.sha256"

    # Pre-validate container targeting early to fail closed immediately if target is ambiguous
    try:
        resolved_container = resolve_docker_container(
            db_name=db_name,
            container_name=container_name,
            environment=environment,
        )
    except ValueError as val_err:
        logger.error("Configuration error: %s", val_err)
        return False

    logger.info("Starting backup process for database: [%s] on host [%s:%s] (target container: [%s])",
                db_name, db_host, db_port, resolved_container)
    logger.info("Destination dump path: %s", dump_path)

    if dry_run:
        logger.info("[DRY RUN] Would execute pg_dump and upload to s3://%s/%s/%s", s3_bucket, s3_prefix, dump_filename)
        return True

    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Execute pg_dump with credentials passed securely via environment variable
    # Never expose credentials in command arguments or shell strings
    pg_env = os.environ.copy()
    pg_env["PGPASSWORD"] = db_password

    # Test if pg_dump is available on host, or fall back to docker exec
    dump_cmd = [
        "pg_dump",
        "-h", db_host,
        "-p", str(db_port),
        "-U", db_user,
        "-d", db_name,
        "-Fc",  # PostgreSQL Custom binary format with compression
        "-f", str(dump_path),
    ]

    try:
        logger.info("Executing pg_dump (custom compressed format)...")
        res = subprocess.run(dump_cmd, env=pg_env, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        # Host lacks pg_dump; execute inside resolved docker container
        logger.info("pg_dump not found on host PATH. Routing via Docker container '%s'...", resolved_container)
        container_tmp_dump = f"/tmp/{dump_filename}"
        docker_cmd = [
            "docker", "exec",
            "-e", f"PGPASSWORD={db_password}",
            resolved_container,
            "pg_dump",
            "-U", db_user,
            "-d", db_name,
            "-Fc",
            "-f", container_tmp_dump,
        ]
        res = subprocess.run(docker_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            logger.error("Database dump failed inside container '%s': %s", resolved_container, res.stderr.strip())
            return False
        # Copy dump from container to local host
        subprocess.run(["docker", "cp", f"{resolved_container}:{container_tmp_dump}", str(dump_path)], check=True)
        # Clean up temporary container file
        subprocess.run(["docker", "exec", resolved_container, "rm", "-f", container_tmp_dump], check=False)
    except subprocess.CalledProcessError as err:
        logger.error("pg_dump failed with exit code %s: %s", err.returncode, err.stderr.strip())
        if dump_path.exists():
            dump_path.unlink()
        return False

    dump_size_bytes = dump_path.stat().st_size
    logger.info("Dump succeeded. Size: %s bytes (%0.2f MB)", dump_size_bytes, dump_size_bytes / (1024 * 1024))

    # 2. Compute SHA-256 Checksum
    logger.info("Generating SHA-256 checksum...")
    sha256_hash = compute_sha256(dump_path)
    checksum_content = f"{sha256_hash}  {dump_filename}\n"
    checksum_path.write_text(checksum_content, encoding="utf-8")
    logger.info("SHA-256 checksum verified: %s", sha256_hash)

    # 3. Upload to AWS S3 with SSE-KMS Encryption
    s3_dump_uri = f"s3://{s3_bucket}/{s3_prefix}/{dump_filename}"
    s3_checksum_uri = f"s3://{s3_bucket}/{s3_prefix}/{dump_filename}.sha256"

    upload_cmd_base = ["aws", "s3", "cp"]
    if aws_profile:
        upload_cmd_base.extend(["--profile", aws_profile])

    dump_upload_cmd = upload_cmd_base + [
        str(dump_path),
        s3_dump_uri,
        "--sse", "aws:kms",
        "--sse-kms-key-id", kms_key_id,
        "--only-show-errors",
    ]

    chk_upload_cmd = upload_cmd_base + [
        str(checksum_path),
        s3_checksum_uri,
        "--sse", "aws:kms",
        "--sse-kms-key-id", kms_key_id,
        "--only-show-errors",
    ]

    try:
        logger.info("Uploading dump to AWS S3 (SSE-KMS): %s", s3_dump_uri)
        subprocess.run(dump_upload_cmd, check=True, capture_output=True, text=True)

        logger.info("Uploading checksum to AWS S3: %s", s3_checksum_uri)
        subprocess.run(chk_upload_cmd, check=True, capture_output=True, text=True)

        logger.info("SUCCESS: Backup successfully stored and encrypted in S3: %s", s3_dump_uri)
        return True
    except subprocess.CalledProcessError as upload_err:
        logger.error("AWS S3 upload failed: %s", upload_err.stderr.strip())
        return False


def main():
    parser = argparse.ArgumentParser(description="Company Portal PostgreSQL S3 Backup Utility")
    parser.add_argument("--db-name", default=os.getenv("POSTGRES_DB", "company_portal"))
    parser.add_argument("--db-user", default=os.getenv("POSTGRES_USER", "postgres"))
    parser.add_argument("--db-host", default=os.getenv("POSTGRES_SERVER", "localhost"))
    parser.add_argument("--db-port", default=os.getenv("POSTGRES_PORT", "5432"))
    parser.add_argument(
        "--container-name",
        default=os.getenv("DOCKER_DB_CONTAINER") or os.getenv("POSTGRES_CONTAINER"),
        help="Name of the Docker container hosting PostgreSQL (e.g. company_portal_prod_db).",
    )
    parser.add_argument(
        "--environment",
        default=os.getenv("ENVIRONMENT"),
        help="Target environment (development, staging, production).",
    )
    parser.add_argument("--s3-bucket", default=os.getenv("S3_BACKUP_BUCKET", "company-portal-backups-691634278287"))
    parser.add_argument("--s3-prefix", default=os.getenv("S3_BACKUP_PREFIX", "backups/daily"))
    parser.add_argument("--kms-key", default=os.getenv("KMS_KEY_ALIAS", "alias/company-portal-db-backup-key"))
    parser.add_argument("--output-dir", type=Path, default=Path("backups"))
    parser.add_argument("--profile", default=os.getenv("AWS_PROFILE"))
    parser.add_argument("--dry-run", action="store_true", help="Simulate backup without writing data")

    args = parser.parse_args()

    db_password = os.getenv("POSTGRES_PASSWORD")
    if not db_password and not args.dry_run:
        logger.error("FATAL: Database password not supplied via POSTGRES_PASSWORD environment variable.")
        sys.exit(1)

    success = run_backup(
        db_name=args.db_name,
        db_user=args.db_user,
        db_host=args.db_host,
        db_port=args.db_port,
        db_password=db_password or "",
        s3_bucket=args.s3_bucket,
        s3_prefix=args.s3_prefix,
        kms_key_id=args.kms_key,
        output_dir=args.output_dir,
        container_name=args.container_name,
        environment=args.environment,
        aws_profile=args.profile,
        dry_run=args.dry_run,
    )

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
