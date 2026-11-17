import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def isolated_token_ledger(tmp_path, monkeypatch):
    """Keep every test's token ledger in a temporary file.

    The embedding service records usage on every provider call, so without this the test suite would
    append real ledger entries to the working tree.
    """
    monkeypatch.setenv("TOKEN_LEDGER_PATH", str(tmp_path / "token-ledger.jsonl"))
