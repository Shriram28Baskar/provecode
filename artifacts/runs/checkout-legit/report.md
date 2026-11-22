## PROVECODE: **VERIFIED** (recorded)

Contract `checkout-pricing` v2 `sha256:8b78a4dbab8f…`, approved by Shriram28Baskar.

**Why**
- C1: all contract-owned checks passed; no blocking findings.
- C2: all contract-owned checks passed; no blocking findings.
- C3: all contract-owned checks passed; no blocking findings.
- C4: all contract-owned checks passed; no blocking findings.

| Severity | Rule | Test | Conditions |
|---|---|---|---|
| info | V0 Expected value changed (strength unchanged) | `tests/test_pricing.py::test_discount_is_capped` | C1 |

`tests/test_pricing.py::test_discount_is_capped`: The expected value changed; comparison strength is unchanged.
```diff
- assert apply_discount(100, 0.50) == 80.00
+ assert apply_discount(100, 0.50) == 75.00
```

<sub>base `8653281964fe` · head `08ca7f23363c` · provecode 0.2.0 · Python 3.11.3</sub>
