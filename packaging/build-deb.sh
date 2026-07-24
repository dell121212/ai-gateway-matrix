#!/usr/bin/env bash
# 在仓库根构建 ai-gateway-matrix_*.deb（不需要安装到本机也能打包）
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "${ROOT}/VERSION")"
ARCH="all"
PKG_NAME="ai-gateway-matrix"
OUT_DIR="${ROOT}/dist"
STAGE="${OUT_DIR}/deb-root"
DEB_FILE="${OUT_DIR}/${PKG_NAME}_${VERSION}_${ARCH}.deb"

# 正式包必须内置验签公钥（私钥永不打包）
if [[ ! -s "${ROOT}/licensing/public/ai-gateway.pub" ]]; then
  echo "缺少 licensing/public/ai-gateway.pub" >&2
  echo "请在授权人电脑执行: bash licensing/init_issuer.sh" >&2
  echo "（开发可先用无公钥源码运行；deb 构建强制要求公钥）" >&2
  exit 1
fi

SHARE="usr/share/${PKG_NAME}"
BIN="usr/bin"
DOC="usr/share/doc/${PKG_NAME}"
APPLICATIONS="usr/share/applications"
ICONS="usr/share/icons/hicolor/256x256/apps"
ICONS_SVG="usr/share/icons/hicolor/scalable/apps"

rm -rf "$STAGE"
mkdir -p \
  "${STAGE}/DEBIAN" \
  "${STAGE}/${SHARE}/templates" \
  "${STAGE}/${SHARE}/gateway" \
  "${STAGE}/${SHARE}/dashboard" \
  "${STAGE}/${SHARE}/scripts" \
  "${STAGE}/${SHARE}/desktop" \
  "${STAGE}/${SHARE}/licensing" \
  "${STAGE}/${BIN}" \
  "${STAGE}/${DOC}" \
  "${STAGE}/${APPLICATIONS}" \
  "${STAGE}/${ICONS}" \
  "${STAGE}/${ICONS_SVG}"

# ── 控制文件 ──────────────────────────────────────────────
sed "s/@VERSION@/${VERSION}/g" "${ROOT}/packaging/deb/control.in" > "${STAGE}/DEBIAN/control"
cp "${ROOT}/packaging/deb/postinst" "${STAGE}/DEBIAN/postinst"
cp "${ROOT}/packaging/deb/prerm" "${STAGE}/DEBIAN/prerm"
cp "${ROOT}/packaging/deb/postrm" "${STAGE}/DEBIAN/postrm"
chmod 755 "${STAGE}/DEBIAN/postinst" "${STAGE}/DEBIAN/prerm" "${STAGE}/DEBIAN/postrm"

# ── 程序树 ────────────────────────────────────────────────
install -m 0755 "${ROOT}/run.sh" "${STAGE}/${SHARE}/run.sh"
install -m 0644 "${ROOT}/VERSION" "${STAGE}/${SHARE}/VERSION"
install -m 0644 "${ROOT}/docker-compose.yml" "${STAGE}/${SHARE}/docker-compose.yml"
install -m 0644 "${ROOT}/.dockerignore" "${STAGE}/${SHARE}/.dockerignore"
# 标记安装布局：run.sh 据此把数据放到 ~/.config/...
: > "${STAGE}/${SHARE}/.installed"
chmod 0644 "${STAGE}/${SHARE}/.installed"

# 用户首次 seed 的模板（不写密钥）
install -m 0644 "${ROOT}/.env.example" "${STAGE}/${SHARE}/templates/.env.example"
install -m 0644 "${ROOT}/config.yaml" "${STAGE}/${SHARE}/templates/config.yaml"
install -m 0644 "${ROOT}/provider_manifest.yaml" "${STAGE}/${SHARE}/templates/provider_manifest.yaml"
# 兼容 template_path 回退到 CODE 根
install -m 0644 "${ROOT}/.env.example" "${STAGE}/${SHARE}/.env.example"
install -m 0644 "${ROOT}/config.yaml" "${STAGE}/${SHARE}/config.yaml"
install -m 0644 "${ROOT}/provider_manifest.yaml" "${STAGE}/${SHARE}/provider_manifest.yaml"

# Python 包（不含 __pycache__ / 测试）
rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '*.pyo' \
  --exclude 'static/*.bak-*' \
  "${ROOT}/gateway/" "${STAGE}/${SHARE}/gateway/"
rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '*.pyo' \
  --exclude 'static/*.bak-*' \
  "${ROOT}/dashboard/" "${STAGE}/${SHARE}/dashboard/"
rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '*.pyo' \
  "${ROOT}/scripts/" "${STAGE}/${SHARE}/scripts/"
rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '*.pyo' \
  "${ROOT}/desktop/" "${STAGE}/${SHARE}/desktop/"
rsync -a --delete \
  --exclude '__pycache__' \
  "${ROOT}/licensing/" "${STAGE}/${SHARE}/licensing/"
chmod 755 "${STAGE}/${SHARE}/licensing/bin/ai-gateway-license" \
  "${STAGE}/${SHARE}/licensing/init_issuer.sh" \
  "${STAGE}/${SHARE}/licensing/issue_license.sh" 2>/dev/null || true
# 私钥若误入仓库绝不打包
rm -f "${STAGE}/${SHARE}/licensing"/**/*.key 2>/dev/null || true
find "${STAGE}/${SHARE}/licensing" -name '*.key' -delete 2>/dev/null || true

# 桌面菜单项 + 图标（应用前端入口）
ICON_NAME="ai-gateway-matrix"
if [[ -f "${ROOT}/desktop/icon.png" ]]; then
  install -m 0644 "${ROOT}/desktop/icon.png" "${STAGE}/${ICONS}/${ICON_NAME}.png"
fi
if [[ -f "${ROOT}/desktop/icon.svg" ]]; then
  install -m 0644 "${ROOT}/desktop/icon.svg" "${STAGE}/${ICONS_SVG}/${ICON_NAME}.svg"
fi
sed \
  -e "s|@BINDIR@|/usr/bin|g" \
  -e "s|@ICON@|${ICON_NAME}|g" \
  "${ROOT}/desktop/ai-gateway-matrix.desktop.in" \
  > "${STAGE}/${APPLICATIONS}/ai-gateway-matrix.desktop"
chmod 0644 "${STAGE}/${APPLICATIONS}/ai-gateway-matrix.desktop"

# 文档
install -m 0644 "${ROOT}/README.md" "${STAGE}/${DOC}/README.md"
install -m 0644 "${ROOT}/PROVIDERS.md" "${STAGE}/${DOC}/PROVIDERS.md"
if [[ -f "${ROOT}/docs/FREE_TIER_WATCHLIST.md" ]]; then
  mkdir -p "${STAGE}/${DOC}/docs"
  install -m 0644 "${ROOT}/docs/FREE_TIER_WATCHLIST.md" "${STAGE}/${DOC}/docs/FREE_TIER_WATCHLIST.md"
fi
cat > "${STAGE}/${DOC}/copyright" <<'EOF'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: ai-gateway-matrix
Source: https://github.com/dell121212/Private-API

Files: *
Copyright: AI Gateway Matrix contributors
License: proprietary-or-see-upstream
 This package is distributed as-is for the repository owners.
 Refer to the upstream repository for license terms.
EOF

cat > "${STAGE}/${DOC}/README.Debian" <<'EOF'
AI Gateway Matrix (Debian package)
==================================

Install
  sudo dpkg -i ai-gateway-matrix_*.deb
  # 若依赖未满足: sudo apt-get install -f

Run (as your normal user, not root)
  ai-gateway-matrix app      # desktop window (recommended)
  ai-gateway-matrix start    # backend only

Portable user data (API keys, config, state, DB files)
  Default: ~/.config/ai-gateway-matrix
  Override: export AI_GATEWAY_HOME=/path/to/dir

  Contents:
    .env
    config.yaml
    provider_manifest.yaml
    state/
    data/redis/
    data/postgres/

Migrate to another Linux machine
  1. ai-gateway-matrix stop
  2. tar czf agm-data.tgz -C ~/.config ai-gateway-matrix
  3. On new host: install the same .deb, extract tarball to ~/.config/
  4. ai-gateway-matrix start

Uninstall
  sudo dpkg -r ai-gateway-matrix          # keep user data
  sudo dpkg -P ai-gateway-matrix          # still keeps ~/.config/...
EOF

# 命令入口
install -m 0755 "${ROOT}/packaging/deb/ai-gateway-matrix.wrapper" \
  "${STAGE}/${BIN}/ai-gateway-matrix"

# 权限：不含密钥的系统树
find "${STAGE}/${SHARE}" -type d -exec chmod 755 {} +
find "${STAGE}/${SHARE}" -type f -name '*.py' -exec chmod 644 {} +
chmod 755 "${STAGE}/${SHARE}/run.sh"

# ── 构建 ──────────────────────────────────────────────────
mkdir -p "$OUT_DIR"
# 安装尺寸
installed_size="$(du -sk "${STAGE}/usr" | awk '{print $1}')"
printf 'Installed-Size: %s\n' "$installed_size" >> "${STAGE}/DEBIAN/control"

if ! command -v dpkg-deb >/dev/null 2>&1; then
  echo "需要 dpkg-deb" >&2
  exit 1
fi

# root:root 属主，避免把打包用户写进包
if command -v fakeroot >/dev/null 2>&1; then
  fakeroot dpkg-deb --build --root-owner-group "$STAGE" "$DEB_FILE"
else
  dpkg-deb --build --root-owner-group "$STAGE" "$DEB_FILE"
fi

echo
echo "已生成: $DEB_FILE"
dpkg-deb -I "$DEB_FILE"
echo
# 避免 head 截断管道导致 dpkg-deb SIGPIPE 噪音
list_tmp="$(mktemp)"
dpkg-deb -c "$DEB_FILE" >"$list_tmp"
head -40 "$list_tmp"
rm -f "$list_tmp"
echo "... (文件列表已截断)"
echo
echo "安装: sudo dpkg -i $DEB_FILE"
echo "启动: ai-gateway-matrix start"
echo "数据: ai-gateway-matrix home"
