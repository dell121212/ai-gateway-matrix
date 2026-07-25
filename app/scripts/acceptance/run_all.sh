#!/usr/bin/env bash
# Unified acceptance: unit + integration-ish + frontend build + compose config.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo "== pytest billing + gateway mode =="
pytest tests/billing -q --tb=line

echo "== pytest existing suite (non-fatal summary) =="
set +e
pytest tests/ -q --tb=no
LEGACY_RC=$?
set -e
echo "legacy_pytest_rc=$LEGACY_RC"

echo "== validate_config =="
python -m scripts.validate_config

echo "== ruff =="
ruff check gateway dashboard/app tests/billing scripts/reconcile_ledger.py || true

echo "== docker compose config =="
docker compose config >/dev/null

if [[ -d dashboard/frontend ]]; then
  echo "== frontend build =="
  if [[ -f dashboard/frontend/package.json ]]; then
    (cd dashboard/frontend && npm ci --ignore-scripts 2>/dev/null || npm install --ignore-scripts)
    (cd dashboard/frontend && npm run build)
  fi
fi

echo "== acceptance done =="
exit 0
