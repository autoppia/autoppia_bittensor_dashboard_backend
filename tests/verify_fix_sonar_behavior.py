#!/usr/bin/env python3
"""
Verify that fix/sonar refactors preserve behavior (no DB/Redis required).
Run: python -m tests.verify_fix_sonar_behavior
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root
root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))


def test_task_solutions_query_params():
    """TaskSolutionsQueryParams has same defaults as former get_tasks_with_solutions kwargs."""
    from app.services.ui.external_tasks_query import TaskSolutionsQueryParams

    p = TaskSolutionsQueryParams()
    assert p.page == 1
    assert p.limit == 50
    assert p.sort_by == "created_at"
    assert p.sort_order == "desc"
    assert p.task_id is None
    assert p.website is None
    assert p.min_score is None
    assert p.max_score is None
    print("  TaskSolutionsQueryParams defaults OK")


def test_seed_utils_builder_signatures():
    """Builders accept only (validator_round_id, record) and return correct types."""
    import inspect

    from app.services.seed_utils import (
        _build_miner_identity_and_snapshot,
        _build_validator_identity_and_snapshot,
    )

    sig_v = inspect.signature(_build_validator_identity_and_snapshot)
    assert list(sig_v.parameters.keys()) == ["validator_round_id", "record"], sig_v.parameters

    sig_m = inspect.signature(_build_miner_identity_and_snapshot)
    assert list(sig_m.parameters.keys()) == ["validator_round_id", "record"], sig_m.parameters
    print("  seed_utils builder signatures OK")


def test_seed_utils_builder_returns():
    """Builders return (identity, snapshot) with expected attributes."""
    from app.services.seed_utils import (
        _build_miner_identity_and_snapshot,
        _build_validator_identity_and_snapshot,
    )

    # ValidatorSeedRecord / MinerSeedRecord minimal (from seed_utils types)
    class FakeValidatorRecord:
        uid = 1
        hotkey = "0xab"
        coldkey = "0xcd"
        name = "V"
        image = ""
        version = "1.0"

    class FakeMinerRecord:
        uid = 2
        hotkey = "0xef"
        coldkey = "0x01"
        name = "M"
        image = ""
        github = ""

    v_identity, v_snapshot = _build_validator_identity_and_snapshot(validator_round_id="vr-1", record=FakeValidatorRecord())
    assert v_identity.uid == 1 and v_identity.hotkey == "0xab"
    assert v_snapshot.validator_uid == 1 and v_snapshot.validator_hotkey == "0xab"

    m_identity, m_snapshot = _build_miner_identity_and_snapshot(validator_round_id="vr-1", record=FakeMinerRecord())
    assert m_identity.uid == 2 and m_identity.hotkey == "0xef"
    assert m_snapshot.miner_uid == 2 and m_snapshot.miner_hotkey == "0xef"
    print("  seed_utils builder returns OK")


def test_db_session_redact_dsn():
    """Session module still has redact and no redundant exception in except (import only)."""
    from app.db import session

    assert hasattr(session, "_redact_dsn")
    out = session._redact_dsn("postgresql://u:p@localhost/db")
    assert "p" not in out or "***" in out
    print("  db.session _redact_dsn OK")


def main():
    print("Verifying fix/sonar behavior (no DB/Redis):")
    test_task_solutions_query_params()
    test_seed_utils_builder_signatures()
    test_seed_utils_builder_returns()
    test_db_session_redact_dsn()
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
