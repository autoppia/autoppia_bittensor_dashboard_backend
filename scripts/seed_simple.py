#!/usr/bin/env python3
"""
Seed validator rounds using .env POSTGRES_* variables.
"""

from scripts.db_utils import get_database_url
from scripts.seed_round import seed_round, seed_multiple_rounds

def main():
    database_url = get_database_url()
    print(f"📡 Using database: {database_url}")

    # Example: seed rounds 1–5 for all validators
    rounds = [1, 2, 3, 4, 5]
    num_miners = 5
    num_tasks = 3

    print(f"🔄 Seeding rounds {rounds} ...")
    seeded = seed_multiple_rounds(
        round_numbers=rounds,
        validator_uids=None,  # all validators
        num_miners=num_miners,
        num_tasks=num_tasks,
    )

    total = sum(len(v) for v in seeded.values())
    print(f"✅ Seeded {len(rounds)} rounds across {total} validator(s).")

if __name__ == "__main__":
    main()
