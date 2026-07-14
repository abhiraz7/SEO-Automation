"""
compute_trend() rules under test (see app/keyword_provider.py):
latest snapshot vs the most recent prior snapshot >= 7 days older;
>10% volume improvement = rising, >10% decline = falling, else stable;
falls back to SERP position (lower = better) when volume is missing;
anything without a valid comparison pair = ("stable", "insufficient_data").

No database or network -- snapshots only need .fetched_at/.volume/.position,
so a plain namespace stands in for the ORM row.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.keyword_provider import compute_trend

NOW = datetime(2026, 7, 14, 12, 0, 0)


def snap(days_ago: float, volume=None, position=None):
    return SimpleNamespace(fetched_at=NOW - timedelta(days=days_ago), volume=volume, position=position)


def test_fewer_than_two_snapshots():
    assert compute_trend([]) == ("stable", "insufficient_data")
    assert compute_trend([snap(0, volume=100)]) == ("stable", "insufficient_data")


def test_no_snapshot_old_enough_to_diff():
    # Two snapshots, but the older one is only 3 days old -- below the 7-day gap.
    assert compute_trend([snap(0, volume=200), snap(3, volume=100)]) == ("stable", "insufficient_data")


def test_rising_on_volume():
    assert compute_trend([snap(0, volume=150), snap(8, volume=100)]) == ("rising", "computed")


def test_falling_on_volume():
    assert compute_trend([snap(0, volume=80), snap(8, volume=100)]) == ("falling", "computed")


def test_stable_within_threshold():
    # +10% exactly is NOT rising -- the rule is strictly greater than 10%.
    assert compute_trend([snap(0, volume=110), snap(8, volume=100)]) == ("stable", "computed")
    assert compute_trend([snap(0, volume=95), snap(8, volume=100)]) == ("stable", "computed")


def test_position_fallback_when_volume_missing():
    # Rank improved 20 -> 10 (lower is better) = rising.
    assert compute_trend([snap(0, position=10), snap(8, position=20)]) == ("rising", "computed")
    # Rank worsened 10 -> 20 = falling.
    assert compute_trend([snap(0, position=20), snap(8, position=10)]) == ("falling", "computed")


def test_no_volume_and_no_position():
    assert compute_trend([snap(0), snap(8)]) == ("stable", "insufficient_data")


def test_baseline_is_most_recent_qualifying_snapshot():
    # 8-day-old snapshot (vol 100) must be the baseline, not the 30-day-old one
    # (vol 1000) -- against 100 the latest 150 is rising; against 1000 it would
    # be falling.
    snaps = [snap(30, volume=1000), snap(8, volume=100), snap(0, volume=150)]
    assert compute_trend(snaps) == ("rising", "computed")


def test_order_independence():
    snaps = [snap(8, volume=100), snap(0, volume=150)]
    assert compute_trend(snaps) == compute_trend(list(reversed(snaps)))
