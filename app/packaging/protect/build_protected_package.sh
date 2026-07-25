#!/usr/bin/env bash
# Build protected payload for customer installers (AUTO-R style).
# Developer gateway/dashboard sources are never modified in-place.
set -euo pipefail

# app/packaging/protect → app
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${1:-$(head -n1 "${ROOT}/VERSION" | tr -d '[:space:]')}"
OUT_BASE="${2:-${ROOT}/build/protected/payload}"
BUILD_ID="${AGM_BUILD_ID:-$(date -u +%Y%m%d%H%M%S)-$(head -c4 /dev/urandom | xxd -p 2>/dev/null || echo rnd)}"

mkdir -p "${ROOT}/build/protected"
rm -rf "${OUT_BASE}"
mkdir -p "${OUT_BASE}"

BYTECODE_ARGS=()
if [[ "${AGM_PROTECT_BYTECODE:-0}" == "1" ]]; then
  BYTECODE_ARGS+=(--bytecode)
fi

python3 "${ROOT}/packaging/protect/transform_sources.py" \
  --root "${ROOT}" \
  --out "${OUT_BASE}" \
  --version "${VERSION}" \
  --build-id "${BUILD_ID}" \
  "${BYTECODE_ARGS[@]}"

# Syntax-check transformed Python
python3 - <<PY
import ast, sys
from pathlib import Path
root = Path("${OUT_BASE}")
errors = 0
count = 0
for p in sorted(root.rglob("*.py")):
    count += 1
    try:
        ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
    except SyntaxError as e:
        print("PARSE FAIL", p, e)
        errors += 1
if not count:
    print("no python files", file=sys.stderr)
    sys.exit(1)
if errors:
    sys.exit(2)
print(f"[protect] parse OK: {count} files")
PY

# Owner-side watermark smoke (rules stay outside payload)
python3 "${ROOT}/packaging/protect/verify_watermark.py" "${OUT_BASE}" || {
  echo "[protect] watermark verify failed" >&2
  exit 3
}

printf '%s\n' "${BUILD_ID}" > "${ROOT}/build/protected/BUILD_ID"
cp -f "${OUT_BASE}/CORE_BUILD_ID" "${ROOT}/build/protected/BUILD_ID" 2>/dev/null || true

echo "[ OK ] protected payload: ${OUT_BASE}"
echo "[ OK ] build_id=${BUILD_ID}"
