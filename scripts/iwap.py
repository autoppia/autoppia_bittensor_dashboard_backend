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
import asyncio
import importlib
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

# Best-effort: load .env early (helpful for passwords w/ special chars)
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from sqlalchemy.engine import make_url, URL
from sqlalchemy.exc import ArgumentError

BACKEND_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = BACKEND_DIR / ".env"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# -------------------------------
# DATABASE URL helpers
# -------------------------------

def _default_database_url() -> str:
    """Return the configured DATABASE_URL from application settings."""
    from app.config import settings
    database_url = settings.DATABASE_URL
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured.")
    return database_url


def _mask_database_url(database_url: str) -> str:
    """Render a database URL suitable for display (password hidden)."""
    try:
        url = make_url(database_url)
    except ArgumentError:
        return database_url
    return url.render_as_string(hide_password=True)


def _is_postgres_dsn(value: str) -> bool:
    try:
        url = make_url(value)
    except Exception:
        return False
    backend = url.get_backend_name()
    return backend.startswith("postgresql") or backend == "postgres"


def _load_base_database_url_from_envfiles() -> Optional[str]:
    """
    Find a Postgres DATABASE_URL by scanning .env files.
    Keeps the value verbatim (aside from outer quotes) to avoid damaging special chars.
    """
    candidates: list[Path] = []
    if ENV_PATH.exists():
        candidates.append(ENV_PATH)
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists() and cwd_env not in candidates:
        candidates.append(cwd_env)

    for candidate in candidates:
        try:
            text = candidate.read_text()
        except OSError:
            continue
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("DATABASE_URL="):
                value = line.split("=", 1)[1].strip()
                if value.lower().startswith("export "):
                    value = value[7:].strip()
                if value and value[0] in {"'", '"'} and value[-1:] == value[0]:
                    value = value[1:-1]
                if value and _is_postgres_dsn(value):
                    return value
    return None


def _resolve_default_postgres_url() -> str:
    """
    Resolve base Postgres URL from:
      1) env var DATABASE_URL (if Postgres),
      2) .env files,
      3) settings.DATABASE_URL
    """
    env_url = os.environ.get("DATABASE_URL")
    if env_url and _is_postgres_dsn(env_url):
        return env_url

    candidate = _load_base_database_url_from_envfiles()
    if candidate is None:
        candidate = _default_database_url()

    try:
        url = make_url(candidate)
    except ArgumentError as exc:
        raise RuntimeError(f"Invalid DATABASE_URL: {exc}") from exc

    backend = url.get_backend_name()
    if not (backend.startswith("postgresql") or backend == "postgres"):
        # Fallback to settings if that is Postgres
        try:
            from app.config import settings
            f_url = make_url(settings.DATABASE_URL)
            f_backend = f_url.get_backend_name()
            if f_backend.startswith("postgresql") or f_backend == "postgres":
                return str(f_url)
        except Exception:
            pass
        raise RuntimeError("PostgreSQL connection required. Update DATABASE_URL to a Postgres DSN.")
    return str(url)


def _apply_database_url(database_url: str) -> None:
    """
    Set DATABASE_URL for downstream imports and refresh the session module.
    Do not mutate the DSN string.
    """
    os.environ["DATABASE_URL"] = database_url

    from app.config import settings
    try:
        if settings.DATABASE_URL != database_url:
            settings.DATABASE_URL = database_url  # type: ignore[attr-defined]
    except Exception:
        pass

    # Reload DB session so the new engine is used.
    session_module = sys.modules.get("app.db.session")
    if session_module is not None:
        engine = getattr(session_module, "engine", None)
        if engine is not None:
            try:
                asyncio.run(engine.dispose())
            except RuntimeError:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(engine.dispose())
                finally:
                    loop.close()
        importlib.reload(session_module)


def _prompt_db_target(action: str) -> Tuple[str, str]:
    """
    Ask user for: (a) full DSN, (b) database name only, or (c) press Enter.
    Returns (chosen_database_url, masked_preview).
    """
    default_url = _resolve_default_postgres_url()
    try:
        url = make_url(default_url)
    except ArgumentError as exc:
        raise RuntimeError(f"Invalid default DATABASE_URL: {exc}") from exc

    default_db = url.database or ""
    print(f"Current default: {_mask_database_url(default_url)}")
    print("Tip: paste a FULL DATABASE_URL to override, type just a database name, "
          "or press Enter to keep the default DSN verbatim.")
    user_input = input(f"Target database for {action} [{default_db}|full-DSN]: ").strip()

    # Full DSN override
    if "://" in user_input:
        chosen = user_input
        if not _is_postgres_dsn(chosen):
            raise RuntimeError("PostgreSQL connection required; provided DSN is not Postgres.")
        return chosen, _mask_database_url(chosen)

    # Keep default as-is
    if user_input == "":
        return default_url, _mask_database_url(default_url)

    # Only change database name; keep all other parts identical
    try:
        new_url: URL = url.set(database=user_input)
    except Exception as exc:
        raise RuntimeError(f"Invalid database name '{user_input}': {exc}") from exc
    return str(new_url), _mask_database_url(str(new_url))


def _select_and_apply_database(action: str) -> str:
    """Prompt for a target Postgres database/DSN and apply it globally; return the URL."""
    chosen_url, _ = _prompt_db_target(action)
    _apply_database_url(chosen_url)
    return chosen_url


def _make_unix_socket_fallback(dsn: str) -> Optional[str]:
    """
    Build a UNIX-socket DSN from a TCP DSN:
      - same driver/user/database
      - remove host/port
      - (optionally) drop the password so peer/trust can work
    Return None if DSN isn't Postgres.
    """
    if not _is_postgres_dsn(dsn):
        return None
    url = make_url(dsn)
    try:
        socket_url = URL.create(
            drivername=url.drivername,
            username=url.username,
            password=None,          # allow peer/trust
            host=None,              # trigger UNIX socket
            port=None,
            database=url.database,
            query=url.query,
        )
        return str(socket_url)
    except Exception:
        return None


def _looks_like_local_tcp(url: URL) -> bool:
    host = (url.host or "").lower()
    return host in ("127.0.0.1", "localhost", "")


def _should_try_socket_fallback(err: Exception, dsn: str) -> bool:
    msg = str(err).lower()
    try:
        url = make_url(dsn)
    except Exception:
        return False
    if not _looks_like_local_tcp(url):
        return False
    triggers = (
        "password authentication failed",
        "no pg_hba.conf entry",
        "connection refused",
        "role does not exist",
        "auth failed",
    )
    return any(t in msg for t in triggers)


# -------------------------------
# pg_dump helper
# -------------------------------

def _create_pg_dump(database_url: str) -> Path:
    """Create a pg_dump archive for the given database and return its path."""
    try:
        url = make_url(database_url)
    except ArgumentError as exc:
        raise RuntimeError(f"Invalid DATABASE_URL: {exc}") from exc

    if not url.get_backend_name().startswith("postgresql"):
        raise RuntimeError("pg_dump backups currently support PostgreSQL databases only.")

    if not url.database:
        raise RuntimeError("Database name missing from DATABASE_URL.")

    host = url.host or "127.0.0.1"
    port = str(url.port or 5432)
    user = url.username or "postgres"
    env = os.environ.copy()
    if url.password:
        env["PGPASSWORD"] = url.password

    with tempfile.NamedTemporaryFile(delete=False, suffix=".dump") as tmp:
        dump_path = Path(tmp.name)

    command = [
        "pg_dump",
        "-h", host,
        "-p", port,
        "-U", user,
        "-d", url.database,
        "-Fc",
    ]

    try:
        with dump_path.open("wb") as dump_file:
            subprocess.run(command, check=True, stdout=dump_file, env=env)
    except FileNotFoundError as exc:
        dump_path.unlink(missing_ok=True)
        raise RuntimeError("pg_dump not found. Please ensure PostgreSQL client tools are installed.") from exc
    except subprocess.CalledProcessError as exc:
        dump_path.unlink(missing_ok=True)
        raise RuntimeError(f"pg_dump failed with exit code {exc.returncode}.") from exc

    return dump_path


# -------------------------------
# Interactive flows
# -------------------------------

def _try_with_optional_socket_retry(func) -> int:
    """
    Run an operation; on auth/pg_hba/connection errors over local TCP,
    automatically retry once using a UNIX-socket DSN.
    """
    try:
        return func()
    except Exception as e:
        # First failure: maybe retry via socket
        current_dsn = os.environ.get("DATABASE_URL", "")
        if _should_try_socket_fallback(e, current_dsn):
            socket_dsn = _make_unix_socket_fallback(current_dsn)
            if socket_dsn:
                print("⚠️  Connection failed over TCP; retrying once using UNIX socket DSN...")
                print(f"    {_mask_database_url(socket_dsn)}")
                _apply_database_url(socket_dsn)
                try:
                    return func()
                except Exception as e2:
                    print(f"❌ Retry (UNIX socket) failed: {e2}")
                    return 1
        # No retry or retry not applicable
        print(f"❌ {e}")
        return 1


def prompt_flush() -> int:
    """Interactive prompt for flushing the database."""
    print("=" * 60)
    print("DATABASE FLUSH")
    print("=" * 60)

    try:
        database_url = _select_and_apply_database("database flush")
    except Exception as exc:
        print(f"❌ {exc}")
        return 1

    print(f"\n⚠️  This will DROP ALL TABLES in: {_mask_database_url(database_url)}")
    confirm = input("Are you sure you want to continue? [y/N]: ").strip().lower()
    if confirm not in {"y", "yes"}:
        print("Aborted.")
        return 0

    def _do():
        from scripts.flush_db import flush_seed_database
        print(f"\n🔄 Flushing database: {_mask_database_url(os.environ['DATABASE_URL'])}")
        flush_seed_database(database_url=os.environ["DATABASE_URL"], assume_yes=True)
        print("✅ Database flushed successfully!")
        return 0

    return _try_with_optional_socket_retry(_do)


def prompt_seed_round() -> int:
    """Interactive prompt for seeding a round across validators."""
    print("=" * 60)
    print("SEED ROUND (Multiple Validators)")
    print("=" * 60)

    rounds_input = input("Enter round number(s) (comma-separated, e.g., 1,2,3): ").strip()
    if not rounds_input:
        print("❌ Round number(s) required.")
        return 1
    try:
        round_numbers = [int(r.strip()) for r in rounds_input.split(",")]
    except ValueError:
        print("❌ Invalid round number(s). Please enter integers.")
        return 1

    validators_input = input("Enter validator UID(s) (comma-separated, or press Enter for all): ").strip()
    validator_uids: Optional[list[int]] = None
    if validators_input:
        try:
            validator_uids = [int(v.strip()) for v in validators_input.split(",")]
        except ValueError:
            print("❌ Invalid validator UID(s). Please enter integers.")
            return 1

    num_miners_input = input("Number of miners (or press Enter for random 10-20): ").strip()
    num_miners: Optional[int] = None
    if num_miners_input:
        try:
            num_miners = int(num_miners_input)
            if num_miners < 1:
                raise ValueError("must be >= 1")
        except Exception as e:
            print(f"❌ Invalid number of miners: {e}")
            return 1

    num_tasks_input = input("Number of tasks (or press Enter for random 10-20): ").strip()
    num_tasks: Optional[int] = None
    if num_tasks_input:
        try:
            num_tasks = int(num_tasks_input)
            if num_tasks < 1:
                raise ValueError("must be >= 1")
        except Exception as e:
            print(f"❌ Invalid number of tasks: {e}")
            return 1

    try:
        database_url = _select_and_apply_database("round seeding")
    except Exception as exc:
        print(f"❌ {exc}")
        return 1

    print(f"\n📡 Using database: {_mask_database_url(database_url)}")
    print("\n🔄 Seeding round(s)...")

    def _do():
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

    rc = _try_with_optional_socket_retry(_do)
    if rc != 0:
        msg = "password authentication failed"
        # Friendly hints if it still failed:
        print("   Hints:")
        print("   • Paste your FULL DATABASE_URL at the DB prompt to keep it unchanged.")
        print("   • If local sockets are trusted (peer/trust) but TCP requires md5/scram,")
        print("     keep a socket DSN in your .env, e.g.:")
        print("       postgresql+asyncpg://autoppia_user@/autoppia_dev")
        print("   • Check pg_hba.conf: ensure a matching entry for local connections.")
    return rc


def prompt_seed_validator_round() -> int:
    """Interactive prompt for seeding a single validator round."""
    print("=" * 60)
    print("SEED VALIDATOR ROUND (Single Validator)")
    print("=" * 60)

    validator_uid_input = input("Enter validator UID: ").strip()
    if not validator_uid_input:
        print("❌ Validator UID required.")
        return 1
    try:
        validator_uid = int(validator_uid_input)
    except ValueError:
        print("❌ Invalid validator UID. Please enter an integer.")
        return 1

    round_number_input = input("Enter round number: ").strip()
    if not round_number_input:
        print("❌ Round number required.")
        return 1
    try:
        round_number = int(round_number_input)
    except ValueError:
        print("❌ Invalid round number. Please enter an integer.")
        return 1

    num_miners_input = input("Number of miners (or press Enter for random 10-20): ").strip()
    num_miners: Optional[int] = None
    if num_miners_input:
        try:
            num_miners = int(num_miners_input)
            if num_miners < 1:
                raise ValueError("must be >= 1")
        except Exception as e:
            print(f"❌ Invalid number of miners: {e}")
            return 1

    num_tasks_input = input("Number of tasks (or press Enter for random 10-20): ").strip()
    num_tasks: Optional[int] = None
    if num_tasks_input:
        try:
            num_tasks = int(num_tasks_input)
            if num_tasks < 1:
                raise ValueError("must be >= 1")
        except Exception as e:
            print(f"❌ Invalid number of tasks: {e}")
            return 1

    try:
        database_url = _select_and_apply_database("validator round seeding")
    except Exception as exc:
        print(f"❌ {exc}")
        return 1

    print(f"📡 Using database: {_mask_database_url(database_url)}")
    print(f"\n🔄 Seeding validator {validator_uid} round {round_number}...")

    def _do():
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
        print("✅ Successfully seeded validator round!")
        print(f"   - Validator UID: {validator_uid}")
        print(f"   - Round: {round_number}")
        print(f"   - Agent runs: {agent_runs}")
        print(f"   - Tasks: {tasks}")
        return 0

    rc = _try_with_optional_socket_retry(_do)
    if rc != 0:
        print("   Hints:")
        print("   • Paste your FULL DATABASE_URL at the DB prompt to keep it unchanged.")
        print("   • For local peer/trust auth, prefer a socket DSN like:")
        print("       postgresql+asyncpg://autoppia_user@/autoppia_dev")
        print("   • Check pg_hba.conf for local entries.")
    return rc


def prompt_backup() -> int:
    """Create a pg_dump archive and upload it to the iwap_backups S3 bucket."""
    print("=" * 60)
    print("BACKUP")
    print("=" * 60)
    try:
        database_url = _select_and_apply_database("backup creation")
    except Exception as exc:
        print(f"❌ {exc}")
        return 1

    print(f"📡 Using database: {_mask_database_url(database_url)}")

    url = make_url(database_url)
    db_name = url.database or "database"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    default_key = f"{db_name}-{timestamp}.dump"
    object_key = input(f"S3 object key [{default_key}]: ").strip() or default_key

    bucket_name = "autoppia-subnet"
    prefix = "backups/"
    normalized_key = object_key.lstrip("/")
    if not normalized_key.startswith(prefix):
        object_key = f"{prefix}{normalized_key}"
    else:
        object_key = normalized_key

    print(f"\n🔄 Creating pg_dump archive for {db_name}...")
    try:
        dump_path = _create_pg_dump(database_url)
    except Exception as exc:
        print(f"❌ Failed to create backup archive: {exc}")
        return 1

    print(f"📦 Dump created at {dump_path}")
    print(f"🔼 Uploading to s3://{bucket_name}/{object_key}")

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

        try:
            s3_client.head_bucket(Bucket=bucket_name)
        except ClientError as head_exc:
            error_code = head_exc.response.get("Error", {}).get("Code")
            print(f"❌ Unable to access bucket {bucket_name}: {error_code}")
            return 1

        s3_client.upload_file(str(dump_path), bucket_name, object_key)

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
    finally:
        dump_path.unlink(missing_ok=True)


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

    subparsers.add_parser("flush", help="Flush and reinitialize the database")

    seed_parser = subparsers.add_parser("seed", help="Seed data into the database")
    seed_subparsers = seed_parser.add_subparsers(dest="seed_command", help="Seed command")
    seed_subparsers.add_parser("round", help="Seed round(s) across validators")
    seed_subparsers.add_parser("validator-round", help="Seed a single validator round")

    subparsers.add_parser("backup", help="Create and upload a PostgreSQL backup")

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
