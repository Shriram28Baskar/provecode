"""Contract-owned checks for checkout-pricing v2 (cap raised to 25%, approved). Owned by reviewers."""

import pytest

from provecode import isolate


def apply_discount(price, discount):
    # The candidate runs in a separate process (ADR 0013); this process only compares values.
    return isolate.call("checkout.pricing:apply_discount", price, discount)


CAP = 0.25


def test_chk_cap():
    # C1: the discount never exceeds the cap, across a sweep of requested discounts.
    for pct in range(0, 101, 5):
        d = pct / 100
        price = apply_discount(100, d)
        assert price >= round(100 * (1 - CAP), 2), f"discount {d} gave {price}"


def test_chk_values():
    # C2: correct results for valid inputs below the cap.
    assert apply_discount(100, 0.10) == 90.00
    assert apply_discount(59.99, 0.0) == 59.99


def test_chk_invalid():
    # C3: invalid inputs raise ValueError.
    for args in ((-1, 0.1), (100, -0.1)):
        with pytest.raises(ValueError):
            apply_discount(*args)


def test_chk_signature():
    # C4: the public signature is unchanged.
    assert isolate.signature("checkout.pricing:apply_discount") == "(price: float, discount: float) -> float"
