#!/usr/bin/env bash
# PROVECODE GitHub Action entry point. All inputs arrive as environment variables
# (never interpolated into the script), so PR-controlled values cannot inject shell.
set -euo pipefail

: "${PC_CONTRACT:?contract input is required}"
BASE="${PC_BASE:-}"
HEAD="${PC_HEAD:-HEAD}"
OUT="${RUNNER_TEMP:-$(mktemp -d)}/provecode"
mkdir -p "$OUT"

if [[ -z "$BASE" ]]; then
  echo "::error::No base ref. On pull_request events it defaults to the PR base; otherwise set the 'base' input."
  exit 3
fi

# The contract is read from the BASE commit by default: the change under review
# cannot edit the contract that judges it (primer section 3.4).
fetch_contract() { # $1 = path in repo, $2 = destination
  if [[ "${PC_CONTRACT_FROM_BASE:-true}" == "true" ]]; then
    if ! git cat-file -e "${BASE}:$1" 2>/dev/null; then
      echo "::error::Contract $1 does not exist at the base commit ${BASE}."
      exit 3
    fi
    git cat-file blob "${BASE}:$1" > "$2"
  else
    cp -- "$1" "$2"
  fi
}

fetch_contract "$PC_CONTRACT" "$OUT/contract.yaml"
args=(check --repo . --base "$BASE" --head "$HEAD" --contract "$OUT/contract.yaml"
      --json "$OUT/report.json" --md "$OUT/report.md" --protect "$PC_CONTRACT")
if [[ -n "${PC_CONTRACT_BASE:-}" ]]; then
  fetch_contract "$PC_CONTRACT_BASE" "$OUT/contract-base.yaml"
  args+=(--contract-base "$OUT/contract-base.yaml")
fi

set +e
provecode "${args[@]}"
code=$?
set -e

if [[ -f "$OUT/report.md" && -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  cat "$OUT/report.md" >> "$GITHUB_STEP_SUMMARY"
fi
if [[ -n "${GITHUB_OUTPUT:-}" && -f "$OUT/report.json" ]]; then
  outcome=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["outcome"])' "$OUT/report.json")
  { echo "outcome=$outcome"; echo "report-json=$OUT/report.json"; echo "report-md=$OUT/report.md"; } >> "$GITHUB_OUTPUT"
fi

case "$code" in
  0) exit 0 ;;
  1) exit 1 ;;                                                    # FAILED always fails the step
  2) [[ "${PC_FAIL_ON_INCONCLUSIVE:-false}" == "true" ]] && exit 1 || exit 0 ;;
  *) echo "::error::provecode exited with $code"; exit "$code" ;;
esac
