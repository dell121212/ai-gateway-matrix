#!/usr/bin/env bash
# 在仓库根构建 ai-gateway-matrix_*.deb（不需要安装到本机也能打包）
set -Eeuo pipefail

# 仓库布局：repo/{run.sh,jiyi.txt,app/,home/} ；本脚本在 app/packaging/
APP="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$(cd -- "${APP}/.." && pwd)"
# 兼容：若 packaging 仍在仓库根（旧布局）
if [[ ! -f "${APP}/VERSION" && -f "${REPO}/VERSION" ]]; then
  APP="$REPO"
fi
ROOT="$APP"
VERSION="$(tr -d '[:space:]' < "${APP}/VERSION")"
ARCH="amd64"
PKG_NAME="ai-gateway-matrix"
OUT_DIR="${APP}/dist"
STAGE="${OUT_DIR}/deb-root"
DEB_FILE="${OUT_DIR}/${PKG_NAME}_${VERSION}_${ARCH}.deb"
FLUTTER_BUNDLE="${APP}/appflowy_gateway/frontend/appflowy_flutter/build/linux/x64/release/bundle"
RUN_SH="${REPO}/run.sh"
if [[ ! -f "$RUN_SH" ]]; then
  RUN_SH="${APP}/run.sh"
fi
if [[ ! -x "${FLUTTER_BUNDLE}/AppFlowy" ]]; then
  echo "缺少 Flutter release bundle: ${FLUTTER_BUNDLE}" >&2
  echo "请先构建 lib/main_gateway.dart。" >&2
  exit 1
fi

# 公钥可选：
# - 有 ai-gateway.pub → 正式 B 端包（未激活不启动）
# - 无公钥 → 个人/社区包（运行时自动跳过授权闸）
PERSONAL_EDITION=0
if [[ ! -s "${APP}/licensing/public/ai-gateway.pub" ]]; then
  PERSONAL_EDITION=1
  echo "提示: 未找到 licensing/public/ai-gateway.pub → 构建「个人版」deb（无授权闸）" >&2
  echo "      B 端正式包请先: bash licensing/init_issuer.sh" >&2
fi

# 开源版默认明文进包（不混淆、不去注释、不强制授权）。
# 仅当显式 AGM_PROTECT=1 时才走可选保护管线（一般不需要）。
USE_PROTECT=0
if [[ "${AGM_PROTECT:-}" == "1" ]]; then
  USE_PROTECT=1
fi
if [[ "$USE_PROTECT" -eq 1 ]]; then
  echo "→ AGM_PROTECT=1：启用可选源码保护管线" >&2
  bash "${APP}/packaging/protect/build_protected_package.sh" "$VERSION" \
    "${APP}/build/protected/payload"
  PROTECT_PAYLOAD="${APP}/build/protected/payload"
else
  echo "→ 开源明文包（默认）" >&2
  PROTECT_PAYLOAD=""
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
  "${STAGE}/${SHARE}/flutter" \
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
install -m 0755 "${RUN_SH}" "${STAGE}/${SHARE}/run.sh"
install -m 0644 "${ROOT}/VERSION" "${STAGE}/${SHARE}/VERSION"
install -m 0644 "${ROOT}/docker-compose.yml" "${STAGE}/${SHARE}/docker-compose.yml"
install -m 0644 "${ROOT}/.dockerignore" "${STAGE}/${SHARE}/.dockerignore"
# 标记安装布局：run.sh 据此把数据放到 ~/.config/...
: > "${STAGE}/${SHARE}/.installed"
chmod 0644 "${STAGE}/${SHARE}/.installed"

# AppFlowy Flutter 桌面外壳：完整 release bundle，业务数据统一走 127.0.0.1:4000。
rsync -a --delete "${FLUTTER_BUNDLE}/" "${STAGE}/${SHARE}/flutter/"
chmod 0755 "${STAGE}/${SHARE}/flutter/AppFlowy"

# 用户首次 seed 的模板（不写密钥）
install -m 0644 "${ROOT}/templates/.env.example" "${STAGE}/${SHARE}/templates/.env.example"
install -m 0644 "${ROOT}/templates/config.yaml" "${STAGE}/${SHARE}/templates/config.yaml"
install -m 0644 "${ROOT}/templates/provider_manifest.yaml" "${STAGE}/${SHARE}/templates/provider_manifest.yaml"
# 兼容 template_path 回退到 CODE 根
install -m 0644 "${ROOT}/templates/.env.example" "${STAGE}/${SHARE}/.env.example"
install -m 0644 "${ROOT}/templates/config.yaml" "${STAGE}/${SHARE}/config.yaml"
install -m 0644 "${ROOT}/templates/provider_manifest.yaml" "${STAGE}/${SHARE}/provider_manifest.yaml"

# Python 程序树：保护载荷 或 明文开发树
if [[ -n "$PROTECT_PAYLOAD" && -d "$PROTECT_PAYLOAD" ]]; then
  rsync -a --delete \
    --exclude '__pycache__' \
    "${PROTECT_PAYLOAD}/gateway/" "${STAGE}/${SHARE}/gateway/"
  rsync -a --delete \
    --exclude '__pycache__' \
    "${PROTECT_PAYLOAD}/dashboard/" "${STAGE}/${SHARE}/dashboard/"
  rsync -a --delete \
    --exclude '__pycache__' \
    --exclude 'test_gateway.py' \
    --exclude 'test_*' \
    --exclude '*_test.py' \
    --exclude 'acceptance' \
    "${PROTECT_PAYLOAD}/scripts/" "${STAGE}/${SHARE}/scripts/"
  rsync -a --delete \
    --exclude '__pycache__' \
    "${PROTECT_PAYLOAD}/desktop/" "${STAGE}/${SHARE}/desktop/"
  if [[ -f "${PROTECT_PAYLOAD}/CORE_BUILD_ID" ]]; then
    install -m 0644 "${PROTECT_PAYLOAD}/CORE_BUILD_ID" "${STAGE}/${SHARE}/CORE_BUILD_ID"
  fi
  if [[ -f "${PROTECT_PAYLOAD}/PROTECTED_BUILD.txt" ]]; then
    install -m 0644 "${PROTECT_PAYLOAD}/PROTECTED_BUILD.txt" "${STAGE}/${SHARE}/PROTECTED_BUILD.txt"
  fi
else
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
    --exclude 'frontend/node_modules' \
    --exclude 'frontend/src' \
    --exclude 'frontend/*.ts' \
    --exclude 'frontend/tsconfig*' \
    --exclude 'frontend/vite.config.*' \
    --exclude 'frontend/package.json' \
    --exclude 'frontend/package-lock.json' \
    "${ROOT}/dashboard/" "${STAGE}/${SHARE}/dashboard/"
  rsync -a --delete \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '*.pyo' \
    --exclude 'test_gateway.py' \
    --exclude 'test_*' \
    --exclude '*_test.py' \
    --exclude 'acceptance' \
    "${ROOT}/scripts/" "${STAGE}/${SHARE}/scripts/"
  rsync -a --delete \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '*.pyo' \
    "${ROOT}/desktop/" "${STAGE}/${SHARE}/desktop/"
fi
# React 控制台构建产物（保护载荷可能已含 dist）
if [[ ! -f "${STAGE}/${SHARE}/dashboard/frontend/dist/index.html" ]]; then
  if [[ -f "${ROOT}/dashboard/frontend/dist/index.html" ]]; then
    mkdir -p "${STAGE}/${SHARE}/dashboard/frontend/dist"
    rsync -a --exclude '*.map' "${ROOT}/dashboard/frontend/dist/" "${STAGE}/${SHARE}/dashboard/frontend/dist/"
  else
    echo "警告: 缺少 dashboard/frontend/dist；deb 内 /console 可能不可用。请先 npm run build" >&2
  fi
fi
# 再扫一遍 map
find "${STAGE}/${SHARE}/dashboard/frontend" -name '*.map' -delete 2>/dev/null || true

rsync -a --delete \
  --exclude '__pycache__' \
  "${ROOT}/licensing/" "${STAGE}/${SHARE}/licensing/"
chmod 755 "${STAGE}/${SHARE}/licensing/bin/ai-gateway-license" \
  "${STAGE}/${SHARE}/licensing/init_issuer.sh" \
  "${STAGE}/${SHARE}/licensing/issue_license.sh" 2>/dev/null || true
# 私钥与「授权人专用」水印规则绝不打包
rm -f "${STAGE}/${SHARE}/licensing"/**/*.key 2>/dev/null || true
find "${STAGE}/${SHARE}/licensing" -name '*.key' -delete 2>/dev/null || true
find "${STAGE}" -name 'watermark_rules.json' -delete 2>/dev/null || true
if [[ "$PERSONAL_EDITION" -eq 1 ]]; then
  # 个人版明确不带公钥，避免半吊子授权状态
  rm -f "${STAGE}/${SHARE}/licensing/public/ai-gateway.pub" 2>/dev/null || true
fi
# 专业后台 / 便携 / 保护说明（不含算法细节）
mkdir -p "${STAGE}/${SHARE}/docs" "${STAGE}/${DOC}/docs"
for doc in PROFESSIONAL_BACKEND.md CLINE_ROO.md PORTABLE_DATA.md; do
  if [[ -f "${ROOT}/docs/${doc}" ]]; then
    install -m 0644 "${ROOT}/docs/${doc}" "${STAGE}/${SHARE}/docs/${doc}"
    install -m 0644 "${ROOT}/docs/${doc}" "${STAGE}/${DOC}/docs/${doc}"
  fi
done
if [[ -f "${ROOT}/packaging/protect/README.md" ]]; then
  install -m 0644 "${ROOT}/packaging/protect/README.md" "${STAGE}/${DOC}/docs/PROTECT.md"
fi

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
install -m 0644 "${REPO}/README.md" "${STAGE}/${DOC}/README.md"
install -m 0644 "${ROOT}/docs/PROVIDERS.md" "${STAGE}/${DOC}/PROVIDERS.md"
install -m 0644 "${ROOT}/appflowy_gateway/LICENSE" \
  "${STAGE}/${DOC}/APPFLOWY-AGPL-3.0.txt"
install -m 0644 "${ROOT}/appflowy_gateway/GATEWAY_MIGRATION.md" \
  "${STAGE}/${DOC}/APPFLOWY-GATEWAY-MIGRATION.md"
install -m 0644 \
  "${ROOT}/appflowy_gateway/frontend/appflowy_flutter/assets/gateway_game_ui/RIGHTS_NOTICE.md" \
  "${STAGE}/${DOC}/GAME-UI-ASSETS-RIGHTS-NOTICE.md"
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

cat > "${STAGE}/${DOC}/README.Debian" <<EOF
AI Gateway Matrix (Debian package)
==================================

Install
  sudo dpkg -i ai-gateway-matrix_*.deb
  # 若依赖未满足: sudo apt-get install -f

Run (as your normal user, not root)
  ai-gateway-matrix app      # desktop window (recommended)
  ai-gateway-matrix start    # backend only

Upgrade (keeps ALL keys and settings automatically)
  sudo dpkg -i ai-gateway-matrix_新版本_all.deb
  # Package only replaces /usr/share/ai-gateway-matrix
  # User data stays in ~/.config/ai-gateway-matrix

Portable user data (API keys, config, state, DB files)
  Default: ~/.config/ai-gateway-matrix
  Override: export AI_GATEWAY_HOME=/path/to/dir

  Contents:
    .env
    config.yaml
    provider_manifest.yaml
    state/
    license/
    data/redis/
    data/postgres/
    PORTABLE.txt

One-file backup / restore
  ai-gateway-matrix backup
  ai-gateway-matrix backup ~/桌面/agm.tgz
  ai-gateway-matrix restore ~/桌面/agm.tgz
  ai-gateway-matrix start

Migrate to another Linux machine
  1. ai-gateway-matrix backup ~/agm.tgz
  2. On new host: install deb, then:
     ai-gateway-matrix restore ~/agm.tgz && ai-gateway-matrix start

Uninstall
  sudo dpkg -r ai-gateway-matrix          # keep user data
  sudo dpkg -P ai-gateway-matrix          # still keeps ~/.config/...

Edition
  $([ "$PERSONAL_EDITION" -eq 1 ] && echo "personal (no license gate)" || echo "licensed (requires .lic)")
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
echo "启动应用: ai-gateway-matrix"
echo "仅启动后端: ai-gateway-matrix start"
echo "数据: ai-gateway-matrix home"
