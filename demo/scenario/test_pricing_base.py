import pytest

from checkout.pricing import apply_discount


def test_discount_is_capped():
    assert apply_discount(100, 0.50) == 80.00


def test_small_discount_applies():
    assert apply_discount(100, 0.10) == 90.00


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        apply_discount(-1, 0.10)
