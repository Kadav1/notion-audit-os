"""Phase I placeholder tests for scoring."""

from notion_audit_os.scoring import CORE_WEIGHTS, MATURITY_BANDS


def test_core_weights_total_is_100():
    assert sum(CORE_WEIGHTS.values()) == 100


def test_core_weights_has_eight_categories():
    assert len(CORE_WEIGHTS) == 8


def test_maturity_bands_cover_zero_to_one_hundred():
    assert MATURITY_BANDS[0][0] == 0
    assert MATURITY_BANDS[-1][1] == 100
