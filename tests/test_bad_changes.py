"""Every bad change in the catalogue must be rejected by the static gates (bugs caught / injected)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import bad_changes  # noqa: E402


@pytest.mark.parametrize("name, result", bad_changes.scoreboard(), ids=[n for n, _ in bad_changes.MUTATIONS])
def test_bad_change_is_caught(name, result):
    assert result, f"not caught: {name}"
