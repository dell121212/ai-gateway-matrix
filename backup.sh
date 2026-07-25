#!/usr/bin/env bash
# 便捷入口：转发到 run.sh backup（用户数据在 home/ 或 AI_GATEWAY_HOME）
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "${ROOT}/run.sh" backup "$@"
