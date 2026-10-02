#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BRANCH=${VALIDATED_BRANCH:-validated-xeon6}

# Bring the last known-good catalog into this run before testing. If the
# publication branch does not exist yet, start with an empty catalog.
if git -C "$ROOT" fetch origin "$BRANCH"; then
  rm -rf "$ROOT/validated"
  mkdir -p "$ROOT/validated"
  # The publication branch stores validated/ at its root.
  git -C "$ROOT" archive "origin/$BRANCH" validated 2>/dev/null | tar -x -C "$ROOT" || true
  echo "Hydrated previous validated catalog from origin/$BRANCH"
else
  echo "No existing $BRANCH branch; starting with an empty validated catalog"
fi
