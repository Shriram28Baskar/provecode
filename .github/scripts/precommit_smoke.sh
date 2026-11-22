#!/usr/bin/env bash
# Checks the pre-commit framework hook (.pre-commit-hooks.yaml + the root pyproject.toml) end to end:
# pre-commit installs this repository the way a user's `repo: .../provecode` does, then a staged
# test weakening must be blocked (S2) and a staged honest change must pass.
#   bash .github/scripts/precommit_smoke.sh [path-to-provecode-checkout]   (needs pre-commit and git)
set -euo pipefail

SRC="$(cd "${1:-$(dirname "$0")/../..}" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"
git init -q
git config user.name smoke
git config user.email smoke@example.invalid
git config commit.gpgsign false
mkdir tests
printf 'def f(x):\n    return x * 2\n' > m.py
printf 'from m import f\n\n\ndef test_f():\n    assert f(2) == 4\n' > tests/test_m.py
git add -A
git commit -qm base

# 1. A staged weakening is blocked.
sed -i 's/== 4/>= 0/' tests/test_m.py
git add tests/test_m.py
set +e
out="$(pre-commit try-repo "$SRC" provecode 2>&1)"
code=$?
set -e
echo "$out"
if [[ $code -eq 0 ]] || ! grep -q "S2" <<<"$out"; then
  echo "::error::the pre-commit hook did not block a staged test weakening (exit $code)"
  exit 1
fi

# 2. A staged honest change passes.
git checkout -q -- tests/test_m.py 2>/dev/null || true
git reset -q --hard
printf 'def f(x):\n    return x + x\n' > m.py
git add m.py
set +e
out="$(pre-commit try-repo "$SRC" provecode 2>&1)"
code=$?
set -e
echo "$out"
if [[ $code -ne 0 ]]; then
  echo "::error::the pre-commit hook blocked an honest change (exit $code)"
  exit 1
fi
echo "pre-commit hook: weakening blocked, honest change passed"
