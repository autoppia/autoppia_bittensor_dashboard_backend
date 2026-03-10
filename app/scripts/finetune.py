"""
Create an OpenAI fine‑tuning job from the exported JSONL dataset.

Behavior:
  - Reads OPENAI_API_KEY from environment (load .env if present).
  - If the dataset JSONL does not exist and --generate-if-missing is enabled
    (default), it will generate it using the DB via
    app.scripts.export_openai_finetune_dataset.
  - Uploads the JSONL file and starts a fine‑tuning job on the specified model.

Usage:
  cd autoppia_bittensor_dashboard_backend
  # Ensure .env contains DATABASE_URL and OPENAI_API_KEY
  python -m app.scripts.finetune \
    --input openai_finetune_dataset.jsonl \
    --model gpt-4o-mini \
    --suffix autoppia-web-actions \
    --min-score 0.5 \
    --max-actions 50

Notes:
  - Requires `openai` Python package. Install: `pip install openai`.
  - By default, it does not wait for completion. Use --wait to poll.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from pathlib import Path

# Default OpenAI model for fine-tuning (used in CLI default, alias mapping, and fallback)
DEFAULT_FINETUNE_MODEL = "gpt-4.1-mini"


def _parse_dotenv_line(line: str) -> tuple[str, str] | None:
    """Parse a single .env line into (key, value) or None if not a valid KEY=value line."""
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, val = line.split("=", 1)
    key, val = key.strip(), val.strip()
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        val = val[1:-1]
    return (key, val)


# Best-effort load environment from .env without requiring python-dotenv
def _load_env_from_dotenv(dotenv_path: str = ".env") -> None:
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(dotenv_path)
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        path = Path(dotenv_path)
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                kv = _parse_dotenv_line(line)
                if kv:
                    os.environ.setdefault(kv[0], kv[1])
    except Exception:  # noqa: BLE001
        pass


def _ensure_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set. Add it to your .env or environment.")

    # Prefer the new OpenAI client if available; fallback to legacy API
    try:
        from openai import OpenAI  # type: ignore

        client = OpenAI(api_key=api_key)
        return ("v1", client)
    except Exception:
        pass
    try:
        import openai  # type: ignore

        openai.api_key = api_key
        return ("legacy", openai)
    except Exception as exc:
        raise RuntimeError("Could not import OpenAI SDK. Install it via `pip install openai`.") from exc


def _upload_file(client_tuple, file_path: str) -> str:
    mode, client = client_tuple
    if mode == "v1":
        with open(file_path, "rb") as f:
            resp = client.files.create(file=f, purpose="fine-tune")
            return resp.id
    else:
        # legacy openai
        with open(file_path, "rb") as f:
            resp = client.File.create(file=f, purpose="fine-tune")
            return resp["id"]


def _create_ft_job(client_tuple, training_file_id: str, model: str, suffix: str | None = None):
    mode, client = client_tuple
    if mode == "v1":
        kwargs = {"training_file": training_file_id, "model": model}
        if suffix:
            kwargs["suffix"] = suffix
        job = client.fine_tuning.jobs.create(**kwargs)
        return job
    else:
        kwargs = {"training_file": training_file_id, "model": model}
        if suffix:
            kwargs["suffix"] = suffix
        job = client.FineTuningJob.create(**kwargs)
        return job


def _job_id_from_resp(resp) -> str:
    if isinstance(resp, dict):
        return resp.get("id")
    return getattr(resp, "id", None)


def _job_status(client_tuple, job_id: str) -> str:
    mode, client = client_tuple
    if mode == "v1":
        job = client.fine_tuning.jobs.retrieve(job_id)
        return getattr(job, "status", "unknown")
    else:
        job = client.FineTuningJob.retrieve(job_id)
        return job.get("status", "unknown")


def _count_lines(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except Exception:  # noqa: BLE001
        return 0


async def _maybe_generate_dataset(
    dataset_path: Path,
    *,
    generate_if_missing: bool,
    min_score: float,
    max_actions: int | None,
    batch_size: int,
) -> bool:
    if dataset_path.exists():
        return True
    if not generate_if_missing:
        return False
    # Lazy import to avoid pulling DB deps unless needed
    from app.scripts.export_openai_finetune_dataset import export_dataset

    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    written = await export_dataset(
        output_path=str(dataset_path),
        min_score=min_score,
        max_actions=max_actions,
        batch_size=batch_size,
    )
    return written > 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create an OpenAI fine‑tuning job from dataset JSONL")
    parser.add_argument(
        "--input",
        default="openai_finetune_dataset.jsonl",
        help="Path to dataset JSONL (generates if missing)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_FINETUNE_MODEL,
        help=f"Base model to fine‑tune (e.g., {DEFAULT_FINETUNE_MODEL})",
    )
    parser.add_argument(
        "--suffix",
        default="autoppia-web-actions",
        help="Suffix/name for the fine‑tuned model",
    )
    parser.add_argument(
        "--generate-if-missing",
        action="store_true",
        default=True,
        help="Generate dataset using DB if missing (default: True)",
    )
    parser.add_argument(
        "--no-generate-if-missing",
        action="store_false",
        dest="generate_if_missing",
        help="Do not generate dataset if missing",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.5,
        help="Minimum evaluation_score to include (default: 0.5)",
    )
    parser.add_argument(
        "--max-actions",
        type=int,
        default=50,
        help="Max actions per example (default: 50)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="DB fetch batch size (default: 1000)",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Wait for the fine‑tuning job to complete",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=15,
        help="Polling interval in seconds when --wait is set (default: 15)",
    )
    return parser.parse_args()


def _ensure_dataset_ready(
    dataset_path: Path,
    *,
    generate_if_missing: bool,
    min_score: float,
    max_actions: int | None,
    batch_size: int,
) -> None:
    """Generate dataset if missing and path is requested; then require file exists and non-empty."""
    if not dataset_path.exists() and generate_if_missing:
        print(f"Dataset {dataset_path} not found. Generating from DB...")
        ok = asyncio.run(
            _maybe_generate_dataset(
                dataset_path,
                generate_if_missing=True,
                min_score=min_score,
                max_actions=max_actions,
                batch_size=batch_size,
            )
        )
        if not ok:
            raise SystemExit("Failed to generate dataset or dataset is empty.")
    if not dataset_path.exists():
        raise SystemExit(f"Dataset not found: {dataset_path}")
    n_lines = _count_lines(dataset_path)
    if n_lines == 0:
        raise SystemExit(f"Dataset is empty: {dataset_path}")
    print(f"Dataset ready: {dataset_path} ({n_lines} lines)")


def _create_job_with_fallback(client_tuple, file_id: str, model: str, suffix: str | None) -> str:
    """Create fine-tuning job; on model unavailability fall back to DEFAULT_FINETUNE_MODEL. Returns job_id."""
    try:
        job_resp = _create_ft_job(client_tuple, file_id, model, suffix=suffix)
    except Exception as e:
        msg = str(e)
        if "model_not_available" in msg or "not available for fine-tuning" in msg:
            if model != DEFAULT_FINETUNE_MODEL:
                print(f"Model '{model}' unavailable. Falling back to '{DEFAULT_FINETUNE_MODEL}'...")
                job_resp = _create_ft_job(client_tuple, file_id, DEFAULT_FINETUNE_MODEL, suffix=suffix)
            else:
                raise
        else:
            raise
    return _job_id_from_resp(job_resp)


def _sanitize_model_name(model: str) -> tuple[str, str | None]:
    """Return (chosen_model, note) and map deprecated aliases to supported models.

    Currently maps 'gpt-4o-mini' → 'gpt-4.1-mini'.
    """
    m = (model or "").strip()
    mapping = {
        "gpt-4o-mini": DEFAULT_FINETUNE_MODEL,
        "gpt-4o-mini-2024-07-18": DEFAULT_FINETUNE_MODEL,
    }
    if m in mapping:
        new_m = mapping[m]
        return new_m, f"Model '{m}' is not fine‑tuneable. Using '{new_m}' instead."
    return m, None


def main() -> None:
    _load_env_from_dotenv()
    args = parse_args()
    dataset_path = Path(args.input)

    _ensure_dataset_ready(
        dataset_path,
        generate_if_missing=args.generate_if_missing,
        min_score=float(args.min_score),
        max_actions=int(args.max_actions) if args.max_actions is not None else None,
        batch_size=int(args.batch_size),
    )

    client_tuple = _ensure_openai_client()
    print("Uploading dataset to OpenAI...")
    file_id = _upload_file(client_tuple, str(dataset_path))
    print(f"Upload complete. File ID: {file_id}")

    chosen_model, note = _sanitize_model_name(args.model)
    if note:
        print(note)
    print(f"Starting fine‑tuning job on model {chosen_model}...")
    job_id = _create_job_with_fallback(client_tuple, file_id, chosen_model, args.suffix)
    print(f"Fine‑tuning job created: {job_id}")

    if args.wait and job_id:
        print("Waiting for job to complete... (Ctrl+C to stop)")
        try:
            while True:
                status = _job_status(client_tuple, job_id)
                print(f"Job {job_id} status: {status}")
                if status in {"succeeded", "failed", "cancelled"}:
                    break
                time.sleep(max(5, int(args.poll_interval)))
        except KeyboardInterrupt:
            print("Stopped waiting. You can check status later in the OpenAI dashboard.")


if __name__ == "__main__":
    main()
