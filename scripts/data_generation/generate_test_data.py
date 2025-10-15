#!/usr/bin/env python3
"""
Legacy compatibility wrapper for generating validator pipeline test data.

This module now delegates to the SOTA-aware implementation found in
``generate_test_data_new.py`` so that both entry-points produce data that
matches the latest schema shape.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
PROJECT_ROOT = BASE_DIR.parent.parent

for candidate in (BASE_DIR, PROJECT_ROOT):
    candidate_str = str(candidate.resolve())
    if candidate_str not in sys.path:
        sys.path.append(candidate_str)

from generate_test_data_new import NewTestDataGenerator  # noqa: E402


class TestDataGenerator(NewTestDataGenerator):
    """Backwards compatible entry point that reuses the new generator."""


async def main() -> None:
    generator = TestDataGenerator()
    await generator.generate_all_data(num_rounds=5)


if __name__ == "__main__":
    asyncio.run(main())
