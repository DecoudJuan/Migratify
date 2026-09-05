"""Shared fixtures.

Everything here is offline. The matching engine is pure, so its tests never
need credentials, a network, or a recorded cassette.
"""

from __future__ import annotations

import pytest

from migratify.config import Thresholds


@pytest.fixture
def thresholds() -> Thresholds:
    return Thresholds()
