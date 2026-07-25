#!/usr/bin/env bash
# 客户交付用 deb：零付费工具链 + 保护构建（不影响日常开发树）。
# 用法（仓库根）:
#   bash packaging/release-deb.sh
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"  # app/
cd "$ROOT"

# 可选前端（已有 dist 则跳过；无 node 也不阻塞保护）
if [[ ! -f dashboard/frontend/dist/index.html ]]; then
  if command -v npm >/dev/null 2>&1 && [[ -f dashboard/frontend/package.json ]]; then
    echo "→ 构建前端 dist…" >&2
    (cd dashboard/frontend && npm run build)
  else
    echo "提示: 无 frontend/dist 且无 npm，/console 可能缺失" >&2
  fi
fi

# 开源发布：明文源码进 deb，不做混淆/水印
export AGM_PROTECT=0
export AGM_PROTECT_BYTECODE=0

bash packaging/build-deb.sh
DEB="$(ls -1t dist/ai-gateway-matrix_*.deb | head -1)"
# 洁癖检查（无强制水印）；有脚本则跑
if [[ -f tests/verify_deb_package.sh ]]; then
  bash tests/verify_deb_package.sh "$DEB" || true
fi
echo
echo "开源交付包: $DEB"
echo "用户数据在 ~/.config/ai-gateway-matrix 或仓库 home/，升级不丢 Key。"
