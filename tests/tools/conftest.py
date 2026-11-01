from pathlib import Path

import pytest

from watchtower.tools.capture import ReplayBackend, load_json

FIXTURES = Path(__file__).parent / "fixtures"
SETS = ["healthy", "S01", "S02", "S03"]


@pytest.fixture(scope="session")
def records():
    """Raw capture records per set, loaded once."""
    return {name: load_json(FIXTURES / f"{name}.json") for name in SETS}


@pytest.fixture
def replay(records):
    """replay("S01") -> a ReplayBackend over that scenario's captured telemetry (no cluster)."""
    return lambda name: ReplayBackend(records[name])
