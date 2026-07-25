#!/usr/bin/env bash
# 解包预演：客户拿到 deb 后不应轻易读到开发源注释 / 私钥 / 水印规则
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEB="${1:-}"
if [[ -z "$DEB" ]]; then
  DEB="$(ls -1t "${ROOT}/dist"/ai-gateway-matrix_*.deb 2>/dev/null | head -1 || true)"
fi
[[ -n "$DEB" && -f "$DEB" ]] || { echo "usage: $0 <deb>"; exit 2; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
dpkg-deb -x "$DEB" "$TMP"
TREE="$TMP/usr/share/ai-gateway-matrix"

echo "[check] tree=$TREE"

fail=0
# 不得有水印规则、git、私钥
if find "$TMP" -name 'watermark_rules.json' | grep -q .; then
  echo "[FAIL] watermark_rules.json shipped"; fail=1
fi
if find "$TMP" -name '.git' -o -name 'id_rsa' -o -name '*.pem' | grep -q .; then
  echo "[FAIL] VCS or key material"; fail=1
fi
if find "$TMP" -name '.env' ! -name '.env.example' | grep -q .; then
  echo "[FAIL] real .env packaged"; fail=1
fi
# 不得有 frontend 源 / map
if find "$TREE" -path '*/frontend/src/*' 2>/dev/null | grep -q .; then
  echo "[FAIL] frontend src packaged"; fail=1
fi
if find "$TREE" -name '*.map' 2>/dev/null | grep -q .; then
  echo "[FAIL] source maps packaged"; fail=1
fi
# 测试脚本（含假 sk- 样例）不应进客户包
if [[ -f "$TREE/scripts/test_gateway.py" ]]; then
  echo "[FAIL] scripts/test_gateway.py shipped in customer package"; fail=1
fi
if find "$TREE" -name 'watermark_rules.json' 2>/dev/null | grep -q .; then
  echo "[FAIL] watermark_rules.json shipped"; fail=1
fi

# 开源明文包：不要求水印；若存在 _wm 则可选校验
if [[ -f "$TREE/gateway/_wm.py" ]]; then
  echo "[ OK ] watermark module present (optional protect build)"
  if command -v python3 >/dev/null && [[ -f "$ROOT/packaging/protect/verify_watermark.py" ]]; then
    python3 "$ROOT/packaging/protect/verify_watermark.py" "$TREE" || fail=1
  fi
else
  echo "[ OK ] open-source plain package (no watermark)"
fi

if [[ "$fail" -ne 0 ]]; then
  echo "[FAIL] deb package hygiene"
  exit 1
fi
echo "[ OK ] deb package hygiene: $DEB"
