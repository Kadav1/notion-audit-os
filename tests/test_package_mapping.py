"""Phase I placeholder tests for recommendation packages."""

from notion_audit_os.models import RECOMMENDED_PACKAGES


def test_locked_package_names_present():
    expected = {
        "Optimization Sprint",
        "Partial Rebuild",
        "Full Rebuild",
        "Governance Add-on",
        "Automation / AI Add-on",
        "No immediate major project needed",
    }
    assert set(RECOMMENDED_PACKAGES) == expected
