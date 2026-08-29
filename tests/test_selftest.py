"""Unit tests for harness/selftest.py."""
from harness import selftest


def test_every_m1_check_passes_and_missing_generators_block_the_freeze():
    """The harness may only certify itself once every milestone is generatable.

    While M2-M5 raise NotImplementedError, selftest must FAIL — the driver freezes harness/ the
    instant this exits 0, and a frozen harness with a missing generator leaves that milestone
    permanently unscoreable. Every M1 check must still pass.
    """
    selftest.FAILURES.clear()
    rc = selftest.main()

    unimplemented = [f for f in selftest.FAILURES if f.endswith("generator implemented")]
    others = [f for f in selftest.FAILURES if f not in unimplemented]
    assert others == [], f"unexpected selftest failures: {others}"

    if unimplemented:
        assert rc == 1
    else:
        assert rc == 0
