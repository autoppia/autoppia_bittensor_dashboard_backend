# IWAP CLI - Quick Reference

## Overview

**IWAP** (Interactive Wrapper for Autoppia) is a simple CLI tool for common database and seeding operations.

## Quick Start

```bash
# From the project root (after `pip install -e .`)
iwap [command]
```

## Commands

### 1. Flush Database

```bash
iwap flush
```

**Interactive Prompts:**
```
============================================================
DATABASE FLUSH
============================================================
Enter database URL (postgresql+asyncpg://autoppia:******@127.0.0.1/autoppia_db): [Press Enter]

⚠️  This will DROP ALL TABLES in: postgresql+asyncpg://autoppia:***@127.0.0.1/autoppia_db
Are you sure you want to continue? [y/N]: y

🔄 Flushing database: postgresql+asyncpg://autoppia:***@127.0.0.1/autoppia_db
Database schema recreated.
✅ Database flushed successfully!
```

---

### 2. Seed Round (Multiple Validators)

```bash
iwap seed round
```

**Interactive Prompts:**
```
============================================================
SEED ROUND (Multiple Validators)
============================================================
Enter round number(s) (comma-separated, e.g., 1,2,3): 1
Enter validator UID(s) (comma-separated, or press Enter for all): [Press Enter for all]
Number of miners (or press Enter for random 10-20): [Press Enter]
Number of tasks (or press Enter for random 10-20): [Press Enter]

🔄 Seeding round(s)...
✅ Seeded round 1 for 3 validator(s).
```

**Example with specific validators:**
```
Enter round number(s) (comma-separated, e.g., 1,2,3): 1,2,3
Enter validator UID(s) (comma-separated, or press Enter for all): 124,125
Number of miners (or press Enter for random 10-20): 15
Number of tasks (or press Enter for random 10-20): 12

🔄 Seeding round(s)...
✅ Seeded 3 round(s) with 6 total validator round(s).
```

---

### 3. Seed Validator Round (Single Validator)

```bash
iwap seed validator-round
```

**Interactive Prompts:**
```
============================================================
SEED VALIDATOR ROUND (Single Validator)
============================================================
Enter validator UID: 124
Enter round number: 1
Number of miners (or press Enter for random 10-20): 15
Number of tasks (or press Enter for random 10-20): 12

🔄 Seeding validator 124 round 1...
✅ Successfully seeded validator round!
   - Validator UID: 124
   - Round: 1
   - Agent runs: 15
   - Tasks: 180
```

---

### 4. Backup (Upload to S3)

```bash
iwap backup
```

**Interactive Prompts & Output:**
```
============================================================
BACKUP
============================================================
Enter database URL (postgresql+asyncpg://autoppia:******@127.0.0.1/autoppia_db): [Press Enter]
S3 object key (autoppia_db-20241020T180000Z.dump): [Press Enter]

🔄 Creating pg_dump archive for autoppia_db...
📦 Dump created at /tmp/tmpabcd.dump
🔼 Uploading to s3://autoppia-subnet/backups/autoppia_db-20241020T180000Z.dump
✅ Backup uploaded successfully!
```

---

## Function Hierarchy

Understanding the seeding functions:

```
seed_multiple_rounds()          ← Top level: Seeds multiple logical rounds
    └─> seed_round()            ← Mid level: Seeds one round across validators
         └─> seed_validator_round()  ← Base level: Seeds single validator round
```

**Examples:**

- **seed_validator_round(124, 1)** → Seeds validator 124, round 1
- **seed_round(1)** → Seeds round 1 for ALL validators  
- **seed_multiple_rounds([1,2,3])** → Seeds rounds 1, 2, 3 for ALL validators

---

## Help Commands

```bash
iwap --help                    # Main help
iwap seed --help              # Seed subcommands help
iwap flush --help             # Flush help
```

---

## Programmatic Usage

If you prefer to use Python directly:

```python
from scripts import (
    seed_validator_round,
    seed_round,
    seed_multiple_rounds,
    flush_seed_database,
)

# Seed single validator round
seed_validator_round(124, 1, num_miners=15, num_tasks=12)

# Seed round across all validators
seed_round(1, num_miners=15, num_tasks=12)

# Seed multiple rounds
seed_multiple_rounds([1, 2, 3], validator_uids=[124, 125])

# Flush database
flush_seed_database("postgresql+asyncpg://autoppia:password@127.0.0.1/autoppia_db", assume_yes=True)
```

---

## File Structure

```
scripts/
├── __init__.py          # Package exports
├── flush_db.py          # Database flush utilities
├── seed_round.py        # Seeding utilities
├── iwap.py              # Interactive CLI
└── README.md            # Detailed documentation
```
