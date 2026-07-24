#!/usr/bin/env bash
# 根据客户申请码签发设备绑定的永久许可证。

set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENT="${ROOT}/licensing/bin/ai-gateway-license"
KEY_DIR="${AI_GATEWAY_ISSUER_DIR:-${XDG_DATA_HOME:-${HOME}/.local/share}/ai-gateway-issuer}"
SECRET_KEY="${AI_GATEWAY_ISSUER_KEY:-${KEY_DIR}/ai-gateway-minisign.key}"
PUBLIC_KEY="${ROOT}/licensing/public/ai-gateway.pub"
REQUEST=""
CUSTOMER="未命名 B 端客户"
UPDATES_UNTIL="permanent"
OUTPUT_DIR=""

usage() {
  echo "用法: bash licensing/issue_license.sh [申请码] [--customer 客户名] [--updates-until YYYY-MM-DD|permanent] [--output-dir 目录]"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --customer) CUSTOMER="${2:?--customer 缺少值}"; shift 2 ;;
    --updates-until) UPDATES_UNTIL="${2:?--updates-until 缺少值}"; shift 2 ;;
    --output-dir) OUTPUT_DIR="${2:?--output-dir 缺少值}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "未知参数: $1" >&2; usage; exit 1 ;;
    *) [ -z "${REQUEST}" ] || { echo "只能提供一个申请码" >&2; exit 1; }; REQUEST="$1"; shift ;;
  esac
done

for cmd in jq minisign age tar; do
  command -v "${cmd}" >/dev/null 2>&1 || { echo "缺少 ${cmd}" >&2; exit 1; }
done
[ -x "${CLIENT}" ] || { echo "找不到许可证客户端" >&2; exit 1; }
[ -s "${SECRET_KEY}" ] || { echo "未初始化签发私钥，请先: bash licensing/init_issuer.sh" >&2; exit 1; }
[ -s "${PUBLIC_KEY}" ] || { echo "缺少签发公钥" >&2; exit 1; }

if [ -z "${REQUEST}" ]; then
  read -r -p "设备申请码: " REQUEST
fi
if [ -t 0 ] && [ "${CUSTOMER}" = "未命名 B 端客户" ]; then
  read -r -p "客户名称 [未命名 B 端客户]: " answer
  [ -z "${answer}" ] || CUSTOMER="${answer}"
fi
if [ "${UPDATES_UNTIL}" != "permanent" ] && ! [[ "${UPDATES_UNTIL}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "更新权益必须是 permanent 或 YYYY-MM-DD" >&2
  exit 1
fi
if [ "${UPDATES_UNTIL}" != "permanent" ] && \
   [ "$(date -u -d "${UPDATES_UNTIL}" +%F 2>/dev/null || true)" != "${UPDATES_UNTIL}" ]; then
  echo "更新权益日期不是有效日历日期: ${UPDATES_UNTIL}" >&2
  exit 1
fi

request_json="$(${CLIENT} decode-request "${REQUEST}")"
device="$(printf '%s' "${request_json}" | jq -r '.device')"
recipient="$(printf '%s' "${request_json}" | jq -r '.recipient')"
platform="$(printf '%s' "${request_json}" | jq -r '.platform')"
app_version="$(printf '%s' "${request_json}" | jq -r '.app_version')"
version="$(head -n1 "${ROOT}/VERSION" | tr -d '[:space:]')"
max_major="$(printf '%s' "${version}" | cut -d. -f1)"
stamp="$(date -u +%Y%m%d%H%M%S)"
suffix="$(od -An -N3 -tx1 /dev/urandom | tr -d ' \n')"
license_id="AGM-${stamp}-${suffix}"
issued_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [ -z "${OUTPUT_DIR}" ]; then
  if command -v xdg-user-dir >/dev/null 2>&1; then OUTPUT_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || true)"; fi
  [ -n "${OUTPUT_DIR}" ] || OUTPUT_DIR="${HOME}/桌面"
fi
mkdir -p "${OUTPUT_DIR}"
safe_customer="$(printf '%s' "${CUSTOMER}" | tr '/\r\n' '___' | cut -c1-40)"
output="${OUTPUT_DIR}/AI-Gateway-Matrix_${safe_customer}_${license_id}.lic"
work="$(mktemp -d "${TMPDIR:-/tmp}/agm-issuer.XXXXXX")"
cleanup() { rm -rf "${work}"; }
trap cleanup EXIT

jq -cn \
  --arg product "AI-Gateway-Matrix" \
  --arg license_id "${license_id}" \
  --arg customer "${CUSTOMER}" \
  --arg device "${device}" \
  --arg edition "Local-B" \
  --argjson max_major "${max_major}" \
  --arg updates_until "${UPDATES_UNTIL}" \
  --arg issued_at "${issued_at}" \
  --arg platform "${platform}" \
  --arg requested_version "${app_version}" \
  '{format:1,product:$product,license_id:$license_id,customer:$customer,device_fingerprint:$device,edition:$edition,perpetual:true,max_major:$max_major,updates_until:$updates_until,issued_at:$issued_at,request_platform:$platform,requested_version:$requested_version}' \
  > "${work}/license.json"

echo "正在签发 ${CUSTOMER} 的 AI Gateway Matrix 永久许可证。"
echo "Minisign 将要求输入你的签发密码。"
minisign -S -s "${SECRET_KEY}" -m "${work}/license.json" \
  -x "${work}/license.minisig" -t "AGM ${license_id}"
minisign -Vm "${work}/license.json" -x "${work}/license.minisig" -p "${PUBLIC_KEY}" >/dev/null
tar -cf "${work}/license.tar" -C "${work}" license.json license.minisig
age -r "${recipient}" -o "${output}" "${work}/license.tar"
chmod 600 "${output}"

echo ""
echo "[OK] 许可证已生成: ${output}"
echo "     客户将该文件放到桌面后重新 start / app 即可。"
