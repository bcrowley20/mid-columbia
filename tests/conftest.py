from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def data_root() -> Path:
    return REPO_ROOT / "data"


@pytest.fixture
def carlson_root(data_root: Path) -> Path:
    return data_root / "Carlson Creek Restoration"


PROJECT_TIMEZONE = "America/Los_Angeles"
