#!/usr/bin/env python3
"""
IWAP - Interactive Wrapper for Autoppia

A simple interactive CLI for common database and seeding operations.

Usage:
    python -m scripts.iwap flush
    python -m scripts.iwap seed round
    python -m scripts.iwap seed validator-round
    python -m scripts.iwap backup
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def prompt_flush() -> int:
    """Interactive prompt for flushing the database."""
    print("=" * 60)
    print("DATABASE FLUSH")
    print("=" * 60)
    
    db_path = input("Enter database path (e.g., autoppia.db): ").strip()
    if not db_path:
        print("❌ Database path cannot be empty.")
        return 1
    
    # Construct the database URL
    database_url = f"sqlite+aiosqlite:///{db_path}"
    
    print(f"\n⚠️  This will DELETE and recreate the database at: {db_path}")
    confirm = input("Are you sure you want to continue? [y/N]: ").strip().lower()
    
    if confirm not in {"y", "yes"}:
        print("Aborted.")
        return 0
    
    try:
        from scripts.flush_db import flush_seed_database
        
        print(f"\n🔄 Flushing database: {db_path}")
        flush_seed_database(database_url=database_url, assume_yes=True)
        print("✅ Database flushed successfully!")
        return 0
    except Exception as e:
        print(f"❌ Error flushing database: {e}")
        return 1


def prompt_seed_round() -> int:
    """Interactive prompt for seeding a round across validators."""
    print("=" * 60)
    print("SEED ROUND (Multiple Validators)")
    print("=" * 60)
    
    # Get round number(s)
    rounds_input = input("Enter round number(s) (comma-separated, e.g., 1,2,3): ").strip()
    if not rounds_input:
        print("❌ Round number(s) required.")
        return 1
    
    try:
        round_numbers = [int(r.strip()) for r in rounds_input.split(",")]
    except ValueError:
        print("❌ Invalid round number(s). Please enter integers.")
        return 1
    
    # Get validator UIDs (optional)
    validators_input = input("Enter validator UID(s) (comma-separated, or press Enter for all): ").strip()
    validator_uids: Optional[list[int]] = None
    if validators_input:
        try:
            validator_uids = [int(v.strip()) for v in validators_input.split(",")]
        except ValueError:
            print("❌ Invalid validator UID(s). Please enter integers.")
            return 1
    
    # Get number of miners (optional)
    num_miners_input = input("Number of miners (or press Enter for random 10-20): ").strip()
    num_miners: Optional[int] = None
    if num_miners_input:
        try:
            num_miners = int(num_miners_input)
        except ValueError:
            print("❌ Invalid number of miners.")
            return 1
    
    # Get number of tasks (optional)
    num_tasks_input = input("Number of tasks (or press Enter for random 10-20): ").strip()
    num_tasks: Optional[int] = None
    if num_tasks_input:
        try:
            num_tasks = int(num_tasks_input)
        except ValueError:
            print("❌ Invalid number of tasks.")
            return 1
    
    print("\n🔄 Seeding round(s)...")
    
    try:
        from scripts.seed_round import seed_round, seed_multiple_rounds
        
        if len(round_numbers) == 1:
            results = seed_round(
                round_number=round_numbers[0],
                validator_uids=validator_uids,
                num_miners=num_miners,
                num_tasks=num_tasks,
            )
            print(f"✅ Seeded round {round_numbers[0]} for {len(results)} validator(s).")
        else:
            seeded = seed_multiple_rounds(
                round_numbers=round_numbers,
                validator_uids=validator_uids,
                num_miners=num_miners,
                num_tasks=num_tasks,
            )
            total = sum(len(results) for results in seeded.values())
            print(f"✅ Seeded {len(round_numbers)} round(s) with {total} total validator round(s).")
        
        return 0
    except Exception as e:
        print(f"❌ Error seeding round(s): {e}")
        return 1


def prompt_seed_validator_round() -> int:
    """Interactive prompt for seeding a single validator round."""
    print("=" * 60)
    print("SEED VALIDATOR ROUND (Single Validator)")
    print("=" * 60)
    
    # Get validator UID
    validator_uid_input = input("Enter validator UID: ").strip()
    if not validator_uid_input:
        print("❌ Validator UID required.")
        return 1
    
    try:
        validator_uid = int(validator_uid_input)
    except ValueError:
        print("❌ Invalid validator UID. Please enter an integer.")
        return 1
    
    # Get round number
    round_number_input = input("Enter round number: ").strip()
    if not round_number_input:
        print("❌ Round number required.")
        return 1
    
    try:
        round_number = int(round_number_input)
    except ValueError:
        print("❌ Invalid round number. Please enter an integer.")
        return 1
    
    # Get number of miners (optional)
    num_miners_input = input("Number of miners (or press Enter for random 10-20): ").strip()
    num_miners: Optional[int] = None
    if num_miners_input:
        try:
            num_miners = int(num_miners_input)
        except ValueError:
            print("❌ Invalid number of miners.")
            return 1
    
    # Get number of tasks (optional)
    num_tasks_input = input("Number of tasks (or press Enter for random 10-20): ").strip()
    num_tasks: Optional[int] = None
    if num_tasks_input:
        try:
            num_tasks = int(num_tasks_input)
        except ValueError:
            print("❌ Invalid number of tasks.")
            return 1
    
    print(f"\n🔄 Seeding validator {validator_uid} round {round_number}...")
    
    try:
        from scripts.seed_round import seed_validator_round
        
        result = seed_validator_round(
            validator_uid=validator_uid,
            round_number=round_number,
            num_miners=num_miners,
            num_tasks=num_tasks,
        )
        
        saved = result.saved_entities
        agent_runs = len(saved.get("agent_evaluation_runs", []))
        tasks = len(saved.get("tasks", []))
        
        print(f"✅ Successfully seeded validator round!")
        print(f"   - Validator UID: {validator_uid}")
        print(f"   - Round: {round_number}")
        print(f"   - Agent runs: {agent_runs}")
        print(f"   - Tasks: {tasks}")
        
        return 0
    except Exception as e:
        print(f"❌ Error seeding validator round: {e}")
        return 1


def _default_database_path() -> Path:
    """Resolve the default SQLite database path from application settings."""
    from app.config import settings

    database_url = settings.DATABASE_URL
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured.")

    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        raise RuntimeError(f"Backup currently supports SQLite databases only (got {url.get_backend_name()})")

    db_path = url.database
    if not db_path or db_path == ":memory:":
        raise RuntimeError("Cannot back up an in-memory database.")

    file_path = Path(db_path).expanduser()
    if not file_path.is_absolute():
        file_path = (BACKEND_DIR / file_path).resolve()
    return file_path


def prompt_backup() -> int:
    """Upload the SQLite database to the iwap_backups S3 bucket."""
    print("=" * 60)
    print("BACKUP")
    print("=" * 60)
    try:
        default_path = _default_database_path()
    except Exception as exc:
        print(f"❌ Unable to determine default database path: {exc}")
        return 1

    db_input = input(f"Enter database path [{default_path}]: ").strip()
    db_path = Path(db_input or str(default_path)).expanduser()
    if not db_path.is_absolute():
        db_path = (BACKEND_DIR / db_path).resolve()

    if not db_path.exists():
        print(f"❌ Database file not found at {db_path}")
        return 1

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    default_key = f"{db_path.stem}-{timestamp}{db_path.suffix or '.db'}"
    object_key = input(f"S3 object key [{default_key}]: ").strip() or default_key

    bucket_name = "iwap_backups"
    print(f"\n🔄 Uploading {db_path} to s3://{bucket_name}/{object_key}")

    try:
        from app.config import settings

        client_kwargs: dict[str, object] = {}
        if settings.AWS_REGION:
            client_kwargs["region_name"] = settings.AWS_REGION
        if settings.AWS_S3_ENDPOINT_URL:
            client_kwargs["endpoint_url"] = settings.AWS_S3_ENDPOINT_URL
        if settings.AWS_ACCESS_KEY_ID:
            client_kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        if settings.AWS_SECRET_ACCESS_KEY:
            client_kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
        if settings.AWS_SESSION_TOKEN:
            client_kwargs["aws_session_token"] = settings.AWS_SESSION_TOKEN

        s3_client = boto3.client("s3", **client_kwargs)

        # Ensure bucket exists.
        try:
            s3_client.head_bucket(Bucket=bucket_name)
        except ClientError as head_exc:
            error_code = head_exc.response.get("Error", {}).get("Code")
            print(f"❌ Unable to access bucket {bucket_name}: {error_code}")
            return 1

        s3_client.upload_file(str(db_path), bucket_name, object_key)

        public_base = settings.AWS_S3_PUBLIC_BASE_URL
        if public_base:
            public_url = f"{public_base.rstrip('/')}/{object_key.lstrip('/')}"
            print(f"✅ Backup uploaded successfully! Public URL: {public_url}")
        else:
            print("✅ Backup uploaded successfully!")

        return 0
    except (NoCredentialsError, ClientError, BotoCoreError, FileNotFoundError) as exc:
        print(f"❌ Failed to upload backup: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="IWAP - Interactive Wrapper for Autoppia",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m scripts.iwap flush
  python -m scripts.iwap seed round
  python -m scripts.iwap seed validator-round
  python -m scripts.iwap backup
        """,
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # Flush command
    subparsers.add_parser("flush", help="Flush and reinitialize the database")
    
    # Seed commands
    seed_parser = subparsers.add_parser("seed", help="Seed data into the database")
    seed_subparsers = seed_parser.add_subparsers(dest="seed_command", help="Seed command")
    seed_subparsers.add_parser("round", help="Seed round(s) across validators")
    seed_subparsers.add_parser("validator-round", help="Seed a single validator round")
    
    # Backup command
    subparsers.add_parser("backup", help="Backup the database (coming soon)")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    if args.command == "flush":
        return prompt_flush()
    elif args.command == "seed":
        if not args.seed_command:
            print("Error: Please specify a seed command (round or validator-round)")
            return 1
        if args.seed_command == "round":
            return prompt_seed_round()
        elif args.seed_command == "validator-round":
            return prompt_seed_validator_round()
    elif args.command == "backup":
        return prompt_backup()
    
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
