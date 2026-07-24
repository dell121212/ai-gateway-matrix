#!/usr/bin/env bash
# 在授权人可信电脑上一次性初始化 Minisign 签发密钥。

set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KEY_DIR="${AI_GATEWAY_ISSUER_DIR:-${XDG_DATA_HOME:-${HOME}/.local/share}/ai-gateway-issuer}"
SECRET_KEY="${KEY_DIR}/ai-gateway-minisign.key"
ISSUER_PUBLIC="${KEY_DIR}/ai-gateway-minisign.pub"
PROJECT_PUBLIC="${ROOT}/licensing/public/ai-gateway.pub"

command -v minisign >/dev/null 2>&1 || {
  echo "缺少 minisign： sudo apt install minisign" >&2
  exit 1
}
mkdir -p "${KEY_DIR}" "$(dirname "${PROJECT_PUBLIC}")"
chmod 700 "${KEY_DIR}"

if [ -s "${SECRET_KEY}" ]; then
  recovery_dir="$(mktemp -d "${TMPDIR:-/tmp}/agm-issuer-public.XXXXXX")"
  recovered="${recovery_dir}/ai-gateway.pub"
  trap 'rm -rf "${recovery_dir}"' EXIT
  echo "检测到已有签发私钥，将验证密码并恢复项目公钥。"
  minisign -R -s "${SECRET_KEY}" -p "${recovered}"
  install -m 0644 "${recovered}" "${ISSUER_PUBLIC}"
  install -m 0644 "${recovered}" "${PROJECT_PUBLIC}"
  echo "[OK] 已从原私钥恢复项目公钥：${PROJECT_PUBLIC}"
  exit 0
fi

if [ -e "${ISSUER_PUBLIC}" ] || [ -e "${PROJECT_PUBLIC}" ]; then
  echo "[ERROR] 找到已有公钥，但本机缺少对应签发私钥：${SECRET_KEY}" >&2
  echo "        请恢复私钥备份；不要另建密钥，否则旧客户许可证将无法延续。" >&2
  exit 2
fi

echo "AI Gateway Matrix 签发密钥初始化"
echo "请设置一个新的强密码；输入时终端不显示字符。"
minisign -G -s "${SECRET_KEY}" -p "${ISSUER_PUBLIC}"
cp "${ISSUER_PUBLIC}" "${PROJECT_PUBLIC}"
chmod 600 "${SECRET_KEY}" "${ISSUER_PUBLIC}"
chmod 644 "${PROJECT_PUBLIC}"

echo ""
echo "[OK] 加密私钥：${SECRET_KEY}"
echo "[OK] 项目公钥：${PROJECT_PUBLIC}"
echo "请离线备份 ${KEY_DIR}；私钥丢失后无法继续为原有客户签发许可证。"
