"""Unit tests for harness/selftest.py."""
from harness import selftest


def test_main_exits_zero_and_reports_no_failures():
    selftest.FAILURES.clear()
    rc = selftest.main()
    assert rc == 0
    assert selftest.FAILURES == []
