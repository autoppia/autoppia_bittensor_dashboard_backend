# Scripts

Utilities for managing the Autoppia backend database and seeding data.

## IWAP - Interactive Wrapper for Autoppia

A simple interactive CLI for common database and seeding operations.

### Quick Start

From the project root (after `pip install -e .`):

```bash
# Using the installed console script (recommended)
iwap [command]

# Or directly with Python
python -m scripts.iwap [command]
```

### Available Commands

#### 1. Flush Database

Interactively flush and reinitialize a database:

```bash
iwap flush
```

You will be prompted for:
- Database path (e.g., `autoppia.db`)
- Confirmation to proceed

#### 2. Seed Round

Seed one or more logical rounds across multiple validators:

```bash
iwap seed round
```

You will be prompted for:
- Round number(s) (comma-separated, e.g., `1,2,3`)
- Validator UID(s) (optional, press Enter for all validators)
- Number of miners (optional, press Enter for random 10-20)
- Number of tasks (optional, press Enter for random 10-20)

#### 3. Seed Validator Round

Seed a single validator round:

```bash
iwap seed validator-round
```

You will be prompted for:
- Validator UID
- Round number
- Number of miners (optional, press Enter for random 10-20)
- Number of tasks (optional, press Enter for random 10-20)

#### 4. Backup

Upload the SQLite database to the `autoppia-subnet/backups/` prefix (credentials pulled from environment/settings):

```bash
iwap backup
```

You will be prompted for:
- Database path (defaults to the configured `DATABASE_URL` location)
- Optional S3 object key (defaults to `<db-name>-<timestamp>.db`)

Requires AWS credentials with write access to the bucket.

### Programmatic Usage

You can also import and use the functions directly in Python:

```python
from scripts import (
    seed_validator_round,    # Seed single validator round
    seed_round,              # Seed round across multiple validators  
    seed_multiple_rounds,    # Seed multiple rounds
    flush_seed_database,     # Reset database
)

# Seed a single validator round
result = seed_validator_round(
    validator_uid=124,
    round_number=1,
    num_miners=15,
    num_tasks=12
)

# Seed round 1 for all validators
results = seed_round(
    round_number=1,
    num_miners=15,
    num_tasks=12
)

# Seed multiple rounds
seeded = seed_multiple_rounds(
    round_numbers=[1, 2, 3],
    validator_uids=[124, 125],
    num_miners=15,
    num_tasks=12
)

# Flush database
flush_seed_database(
    database_url="sqlite+aiosqlite:///autoppia.db",
    assume_yes=True
)
```

### CLI with Arguments (Non-Interactive)

For automation, you can also use the non-interactive CLIs:

```bash
# Seed rounds with arguments
python -m scripts.seed_round --round 1 2 3 --validators 124 125 --num-miners 15 --num-tasks 12

# Flush database with arguments
python -m scripts.flush_db --database-url sqlite+aiosqlite:///autoppia.db --yes
```
