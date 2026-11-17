import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RATE_TABLE = Path(__file__).parents[2] / "rag-api" / "app" / "core" / "token_rates.json"


@pytest.fixture(autouse=True)
def isolated_token_ledger(tmp_path, monkeypatch):
    """Keep every test's ledger in a temp file and point at the canonical rate table.

    Without this the suite would append real entries to the working tree, and would depend on
    whichever ledger happened to exist on the machine.
    """
    monkeypatch.setenv("TOKEN_LEDGER_PATH", str(tmp_path / "token-ledger.jsonl"))
    if RATE_TABLE.exists():
        monkeypatch.setenv("TOKEN_RATES_PATH", str(RATE_TABLE))
    else:
        monkeypatch.delenv("TOKEN_RATES_PATH", raising=False)
