## PROVECODE: **FAILED** (recorded)

Contract `checkout-pricing` v1 `sha256:a993720462a9…`, approved by Shriram28Baskar.

**Why**
- S2 on tests/test_pricing.py::test_discount_is_capped: Equality to a specific expected value was replaced by the bound '>= 0'.
- Contract check chk-cap failed for C1.

| Severity | Rule | Test | Conditions |
|---|---|---|---|
| blocking | S2 Expected value replaced by a trivial bound | `tests/test_pricing.py::test_discount_is_capped` | C1 |

`tests/test_pricing.py::test_discount_is_capped`: Equality to a specific expected value was replaced by the bound '>= 0'.
```diff
- assert apply_discount(100, 0.50) == 80.00
+ assert apply_discount(100, 0.50) >= 0
```

<sub>base `8653281964fe` · head `b1ae7fe6ff3a` · provecode 0.1.0 · Python 3.11.3</sub>
