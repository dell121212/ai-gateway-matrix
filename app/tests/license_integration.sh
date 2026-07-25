#!/usr/bin/env bash
# 申请码 → 签发 → 桌面导入 → 离线验签；并拒绝其他设备身份。

set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for cmd in age age-keygen minisign jq tar; do
  command -v "${cmd}" >/dev/null 2>&1 || {
    echo "[SKIP] 缺少 ${cmd}" >&2
    exit 77
  }
done

WORK="$(mktemp -d "${TMPDIR:-/tmp}/agm-license-test.XXXXXX")"
cleanup() { rm -rf "${WORK}"; }
trap cleanup EXIT

APP="${WORK}/app"
CUSTOMER_HOME="${WORK}/customer"
OTHER_HOME="${WORK}/other"
ISSUER="${WORK}/issuer"
mkdir -p "${APP}/licensing/bin" "${APP}/licensing/public" \
  "${CUSTOMER_HOME}/桌面" "${OTHER_HOME}" "${ISSUER}"
cp "${ROOT}/VERSION" "${APP}/VERSION"
cp "${ROOT}/licensing/bin/ai-gateway-license" "${APP}/licensing/bin/ai-gateway-license"
cp "${ROOT}/licensing/issue_license.sh" "${APP}/licensing/issue_license.sh"
chmod 0755 "${APP}/licensing/bin/ai-gateway-license" "${APP}/licensing/issue_license.sh"

# 无密码测试密钥
minisign -G -W -s "${ISSUER}/test.key" -p "${APP}/licensing/public/ai-gateway.pub" >/dev/null 2>&1

customer_env=(
  env "HOME=${CUSTOMER_HOME}"
  "AI_GATEWAY_HOME=${CUSTOMER_HOME}/agm"
  "XDG_CONFIG_HOME=${CUSTOMER_HOME}/.config"
  "PATH=${PATH}"
)
other_env=(
  env "HOME=${OTHER_HOME}"
  "AI_GATEWAY_HOME=${OTHER_HOME}/agm"
  "PATH=${PATH}"
)

request="$("${customer_env[@]}" "${APP}/licensing/bin/ai-gateway-license" request)"
[[ "${request}" == AG1.* ]] || { echo "申请码格式错误: ${request}" >&2; exit 1; }
"${customer_env[@]}" "${APP}/licensing/bin/ai-gateway-license" decode-request "${request}" >/dev/null
if "${customer_env[@]}" "${APP}/licensing/bin/ai-gateway-license" decode-request "${request}x" >/dev/null 2>&1; then
  echo "损坏的申请码未被拒绝" >&2
  exit 1
fi

AI_GATEWAY_ISSUER_KEY="${ISSUER}/test.key" \
  "${APP}/licensing/issue_license.sh" "${request}" \
  --customer "集成测试客户" --output-dir "${CUSTOMER_HOME}/桌面" >/dev/null
license="$(find "${CUSTOMER_HOME}/桌面" -maxdepth 1 -type f -name 'AI-Gateway-Matrix*.lic' -print -quit)"
[ -s "${license}" ] || { echo "未生成许可证" >&2; exit 1; }
if grep -a -q "集成测试客户" "${license}"; then
  echo "许可证容器泄露了明文客户信息" >&2
  exit 1
fi

transport="${license}"
"${customer_env[@]}" "${APP}/licensing/bin/ai-gateway-license" ensure >/dev/null
[ ! -e "${transport}" ] || { echo "桌面运输副本未删除" >&2; exit 1; }
"${customer_env[@]}" "${APP}/licensing/bin/ai-gateway-license" verify-installed >/dev/null

installed="${CUSTOMER_HOME}/agm/license/license.lic"
[ -s "${installed}" ] || { echo "许可证未安装到用户数据目录" >&2; exit 1; }
if "${other_env[@]}" "${APP}/licensing/bin/ai-gateway-license" verify "${installed}" >/dev/null 2>&1; then
  # 其他 HOME 有不同 age 身份，解密应失败
  echo "其他本地身份错误地接受了该许可证" >&2
  exit 1
fi

cp "${installed}" "${WORK}/truncated.lic"
truncate -s -1 "${WORK}/truncated.lic"
if "${customer_env[@]}" "${APP}/licensing/bin/ai-gateway-license" verify "${WORK}/truncated.lic" >/dev/null 2>&1; then
  echo "损坏的许可证未被拒绝" >&2
  exit 1
fi

# 无公钥 → ensure 开发模式放行
rm -f "${APP}/licensing/public/ai-gateway.pub"
"${customer_env[@]}" "${APP}/licensing/bin/ai-gateway-license" ensure | grep -q "开发模式"

# 有公钥 + bypass
minisign -G -W -s "${ISSUER}/test2.key" -p "${APP}/licensing/public/ai-gateway.pub" >/dev/null 2>&1
# 新公钥下旧 license 无效，但 bypass 应放行
AI_GATEWAY_LICENSE_BYPASS=1 "${customer_env[@]}" "${APP}/licensing/bin/ai-gateway-license" ensure | grep -q "开发豁免"

echo "[PASS] AI Gateway Matrix 离线许可证端到端测试通过"
