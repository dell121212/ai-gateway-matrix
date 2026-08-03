#!/usr/bin/env bash
# AI Gateway Matrix 启动器（仓库根）
# 根目录仅保留 README / *.sh / jiyi.txt
#   程序代码: <repo>/app
#   用户数据: <repo>/home  （或 AI_GATEWAY_HOME / 安装后 ~/.config/...）

set -Eeuo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# 安装布局：deb 把 run.sh 放在 /usr/share/.../run.sh，代码与 run.sh 同目录
if [[ -d "${REPO_ROOT}/app/gateway" ]]; then
    CODE_DIR="${REPO_ROOT}/app"
    SOURCE_LAYOUT=1
else
    CODE_DIR="$REPO_ROOT"
    SOURCE_LAYOUT=0
fi

STARTUP_TIMEOUT="${STARTUP_TIMEOUT:-180}"
COMPOSE_STARTED=false
COMMAND="app"

if [[ -t 1 ]]; then
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    RED='\033[0;31m'
    BOLD='\033[1m'
    RESET='\033[0m'
else
    GREEN=''
    YELLOW=''
    RED=''
    BOLD=''
    RESET=''
fi

info() { printf '%b\n' "${GREEN}✓${RESET} $*"; }
warn() { printf '%b\n' "${YELLOW}⚠${RESET} $*"; }
die() { printf '%b\n' "${RED}✗${RESET} $*" >&2; exit 1; }

is_installed_layout() {
    [[ -f "${CODE_DIR}/.installed" ]] || [[ "$CODE_DIR" == /usr/share/ai-gateway-matrix ]]
}

default_data_dir() {
    if is_installed_layout; then
        printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/ai-gateway-matrix"
    elif [[ "$SOURCE_LAYOUT" -eq 1 ]]; then
        printf '%s\n' "${REPO_ROOT}/home"
    else
        printf '%s\n' "$CODE_DIR"
    fi
}

resolve_data_dir() {
    if [[ -n "${AI_GATEWAY_HOME:-}" ]]; then
        printf '%s\n' "$AI_GATEWAY_HOME"
        return
    fi
    default_data_dir
}

usage() {
    local prog
    prog="$(basename -- "$0")"
    if is_installed_layout || [[ "$prog" == "ai-gateway-matrix" ]]; then
        prog="ai-gateway-matrix"
    else
        prog="./run.sh"
    fi
    cat <<EOF
用法: ${prog} [命令]

命令:
  app       打开 Appica 桌面控制台（推荐；自动 start 后端）
  flutter   打开 Flutter 兼容界面
  start     仅启动网关服务（无窗口；首次会初始化用户数据目录）
  stop      停止全部容器
  restart   重启服务
  status    查看容器状态
  doctor    只读检查配置、Provider、权限与在线状态
  logs      跟踪日志（可附 docker compose logs 参数）
  backup    一键打包用户数据为 .tgz
  restore   从 .tgz 恢复用户数据
  jiyi      管理自动同步的 jiyi.txt（设置+Key 单文件迁移凭证）
  license   （可选）离线授权实验命令；开源版启动不校验
  home      打印用户数据目录
  version   打印版本

布局（源码仓库）:
  ${REPO_ROOT}/README.md  run.sh  backup.sh  jiyi.txt
  ${REPO_ROOT}/app/       程序代码
  ${REPO_ROOT}/home/      用户数据（Key/配置/state）

记忆文件:
  ${prog} jiyi save | load | path | list
  start 后会自动生成并持续同步；正常使用无需手动 save。

升级 deb 不覆盖用户数据目录。
EOF
}

show_docker_install_help() {
    cat >&2 <<'EOF'
请先安装并启动 Docker：
  https://docs.docker.com/engine/install/
EOF
}

show_diagnostics() {
    if [[ "$COMPOSE_STARTED" == true ]]; then
        printf '\n%b\n' "${YELLOW}最近的容器状态与日志：${RESET}" >&2
        "${COMPOSE[@]}" ps >&2 || true
        "${COMPOSE[@]}" logs --tail=100 >&2 || true
    fi
}

template_path() {
    local name="$1"
    if [[ -f "${CODE_DIR}/templates/${name}" ]]; then
        printf '%s\n' "${CODE_DIR}/templates/${name}"
    elif [[ -f "${CODE_DIR}/${name}" ]]; then
        printf '%s\n' "${CODE_DIR}/${name}"
    else
        return 1
    fi
}

seed_user_data() {
    local tpl
    mkdir -p "${DATA_DIR}/state" "${DATA_DIR}/data/redis" "${DATA_DIR}/data/postgres"
    chmod 700 "$DATA_DIR" 2>/dev/null || true

    if [[ ! -f "${DATA_DIR}/.env" ]]; then
        tpl="$(template_path .env.example)" || die "缺少 .env.example 模板"
        cp "$tpl" "${DATA_DIR}/.env"
        chmod 600 "${DATA_DIR}/.env"
        info "已创建 ${DATA_DIR}/.env"
    fi
    if [[ ! -f "${DATA_DIR}/config.yaml" ]]; then
        tpl="$(template_path config.yaml)" || die "缺少 config.yaml 模板"
        cp "$tpl" "${DATA_DIR}/config.yaml"
        info "已创建 ${DATA_DIR}/config.yaml"
    fi
    if [[ ! -f "${DATA_DIR}/provider_manifest.yaml" ]]; then
        tpl="$(template_path provider_manifest.yaml)" || die "缺少 provider_manifest.yaml 模板"
        cp "$tpl" "${DATA_DIR}/provider_manifest.yaml"
        info "已创建 ${DATA_DIR}/provider_manifest.yaml"
    fi
    [[ -w "${DATA_DIR}/state" ]] || die "state 目录不可写：${DATA_DIR}/state"

    if [[ ! -f "${DATA_DIR}/PORTABLE.txt" ]]; then
        cat > "${DATA_DIR}/PORTABLE.txt" <<'PORTABLE'
AI Gateway Matrix — 用户数据目录
设置与 Key 会自动同步到仓库根 jiyi.txt；迁移后用 ./run.sh jiyi load 恢复。
PORTABLE
    fi
}

setup_compose() {
    export AI_GATEWAY_CODE="$CODE_DIR"
    export AI_GATEWAY_HOME="$DATA_DIR"
    export AI_GATEWAY_JIYI_PATH="$(resolve_jiyi_path)"
    export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-ai-gateway-matrix}"
    export HOST_UID="$(id -u)"
    export HOST_GID="$(id -g)"

    if docker compose version >/dev/null 2>&1; then
        COMPOSE=(
            docker compose
            --project-directory "$DATA_DIR"
            -f "${CODE_DIR}/docker-compose.yml"
        )
    elif command -v docker-compose >/dev/null 2>&1; then
        COMPOSE=(
            docker-compose
            --project-directory "$DATA_DIR"
            -f "${CODE_DIR}/docker-compose.yml"
        )
    else
        die "未找到 Docker Compose，请安装 Docker Compose v2"
    fi
}

require_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        show_docker_install_help
        die "未找到 Docker"
    fi
    if ! docker info >/dev/null 2>&1; then
        die "无法连接 Docker；请启动 Docker，并确认当前用户可执行 docker info"
    fi
    setup_compose
}

cmd_home() { printf '%s\n' "$DATA_DIR"; }

cmd_version() {
    if [[ -f "${CODE_DIR}/VERSION" ]]; then
        cat "${CODE_DIR}/VERSION"
    else
        printf 'unknown\n'
    fi
}

license_tool() {
    local tool="${CODE_DIR}/licensing/bin/ai-gateway-license"
    [[ -x "$tool" ]] || die "找不到许可证工具：${tool}"
    export AI_GATEWAY_HOME="$DATA_DIR"
    # 授权工具以 APP_ROOT=licensing/../.. 解析；安装布局 CODE_DIR 正确
    "$tool" "$@"
}

# 开源版：不强制授权。保留 license 子命令供可选实验，启动不再拦截。
require_license_or_die() {
    return 0
}

cmd_license() {
    if [[ $# -eq 0 ]]; then
        license_tool status
        return
    fi
    license_tool "$@"
}

# 从原 run.sh 复制核心 start 逻辑 — 读取完整 ensure secrets 段
# shellcheck source=app/run_lib.sh
# 为减少重复，内嵌精简版 start/stop

cmd_status() {
    require_docker
    "${COMPOSE[@]}" ps
}

cmd_logs() {
    require_docker
    "${COMPOSE[@]}" logs "$@"
}

cmd_stop() {
    require_docker
    "${COMPOSE[@]}" down
    info "已停止"
}

flutter_bundle_path() {
    if [[ "$SOURCE_LAYOUT" -eq 1 ]]; then
        printf '%s\n' \
            "${CODE_DIR}/appflowy_gateway/frontend/appflowy_flutter/build/linux/x64/release/bundle"
    else
        printf '%s\n' "${CODE_DIR}/flutter"
    fi
}

check_flutter_runtime_dependencies() {
    local flutter_bundle="$1"
    local missing_libraries missing_list install_hint=""

    command -v ldd >/dev/null 2>&1 || return 0
    missing_libraries="$({
        LD_LIBRARY_PATH="${flutter_bundle}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}" \
            ldd "${flutter_bundle}/AppFlowy" 2>/dev/null || true
    } | awk '$2 == "=>" && $3 == "not" && $4 == "found" { print $1 }')"
    [[ -z "$missing_libraries" ]] && return 0

    missing_list="${missing_libraries//$'\n'/, }"
    if grep -qx 'libkeybinder-3.0.so.0' <<<"$missing_libraries"; then
        install_hint=$'\nZorin/Ubuntu 安装命令：sudo apt-get install -y libkeybinder-3.0-0'
    fi
    die "Flutter 桌面应用缺少系统运行库：${missing_list}${install_hint}"
}

check_flutter_release_freshness() {
    local flutter_bundle="$1"
    local flutter_project release_library newer_source

    [[ "$SOURCE_LAYOUT" -eq 1 ]] || return 0
    flutter_project="${CODE_DIR}/appflowy_gateway/frontend/appflowy_flutter"
    release_library="${flutter_bundle}/lib/libapp.so"
    [[ -f "$release_library" ]] || return 0

    newer_source="$(find \
        "${flutter_project}/lib/main_gateway.dart" \
        "${flutter_project}/lib/gateway_matrix" \
        -type f -newer "$release_library" -print -quit 2>/dev/null || true)"
    [[ -z "$newer_source" ]] && return 0

    die "Flutter Release 早于界面源码：${newer_source}
请先用 Flutter 3.27.4 重新构建：flutter build linux --release --target lib/main_gateway.dart"
}

ensure_app_backend() {
    require_docker
    require_license_or_die
    # 旧版本升级后可能已有其它容器在跑，但尚无 jiyi-sync；此时也需 reconcile。
    if ! "${COMPOSE[@]}" ps --status running --services jiyi-sync 2>/dev/null \
        | grep -qx "jiyi-sync"; then
        cmd_start
    fi
}

cmd_app() {
    local desktop_entry="${CODE_DIR}/desktop/app.py"
    ensure_app_backend
    command -v python3 >/dev/null 2>&1 || die "缺少 python3，无法启动 Appica 桌面窗口"
    [[ -f "$desktop_entry" ]] || die "缺少桌面入口：${desktop_entry}"
    export AI_GATEWAY_HOME="$DATA_DIR"
    export AI_GATEWAY_CODE="$CODE_DIR"
    info "正在打开 Appica 桌面控制台"
    exec python3 "$desktop_entry" --no-start "$@"
}

cmd_flutter() {
    local flutter_bundle launcher
    ensure_app_backend
    export AI_GATEWAY_HOME="$DATA_DIR"
    export AI_GATEWAY_CODE="$CODE_DIR"
    flutter_bundle="$(flutter_bundle_path)"
    if [[ ! -x "${flutter_bundle}/AppFlowy" ]] \
        || [[ ! -d "${flutter_bundle}/lib" ]] \
        || [[ ! -d "${flutter_bundle}/data" ]]; then
        die "缺少完整 Flutter Release：${flutter_bundle}（请重新构建或安装 1.1.0 deb）"
    fi

    if is_installed_layout && [[ -x /usr/bin/ai-gateway-matrix ]]; then
        launcher="/usr/bin/ai-gateway-matrix"
    else
        launcher="${REPO_ROOT}/run.sh"
    fi
    export AI_GATEWAY_LAUNCHER="$launcher"
    export LD_LIBRARY_PATH="${flutter_bundle}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
    check_flutter_release_freshness "$flutter_bundle"
    check_flutter_runtime_dependencies "$flutter_bundle"
    info "正在打开 Flutter 桌面应用"
    exec "${flutter_bundle}/AppFlowy" "$@"
}

# --- 以下 ensure_env / start 逻辑保持与原先一致（精简引用 CODE_DIR/DATA_DIR）---
# 将原先 run.sh 中 ensure 大段通过 source 已移动的库太重；直接保留关键路径。

ensure_runtime_env() {
    local ENV_FILE="${DATA_DIR}/.env"
    local ENV_EXAMPLE
    ENV_EXAMPLE="$(template_path .env.example)" || ENV_EXAMPLE=""
    [[ -f "$ENV_FILE" ]] || die "缺少 ${ENV_FILE}"

    if [[ -n "$ENV_EXAMPLE" && -f "$ENV_EXAMPLE" ]]; then
        local missing_env_lines
        missing_env_lines="$(awk '
            function env_name(line, name) {
                if (line !~ /^[[:space:]]*[A-Z][A-Z0-9_]*[[:space:]]*=/) return ""
                name = line
                sub(/^[[:space:]]*/, "", name)
                sub(/[[:space:]]*=.*$/, "", name)
                return name
            }
            NR == FNR {
                name = env_name($0)
                if (name != "") existing[name] = 1
                next
            }
            {
                name = env_name($0)
                if (name != "" && !existing[name]) {
                    print $0
                    existing[name] = 1
                }
            }
        ' "$ENV_FILE" "$ENV_EXAMPLE")" || true
        if [[ -n "$missing_env_lines" ]]; then
            printf '\n# --- run.sh 从新版 .env.example 补充 ---\n%s\n' "$missing_env_lines" >> "$ENV_FILE"
            info "已向 .env 补充新配置项（已有值未覆盖）"
        fi
    fi

    ensure_secret() {
        local name="$1" prefix="${2:-}"
        local current generated temp_file
        current="$(awk -F= -v k="$name" '$1 == k {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE" 2>/dev/null || true)"
        current="$(printf '%s' "$current" | tr -d '[:space:]')"
        if [[ -n "$current" ]]; then
            return 0
        fi
        generated="${prefix}$(openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | xxd -p)"
        temp_file="$(mktemp)"
        awk -v wanted="$name" -v value="$generated" '
            BEGIN { found = 0 }
            $0 ~ "^[[:space:]]*" wanted "[[:space:]]*=" {
                print wanted "=" value
                found = 1
                next
            }
            { print }
            END { if (!found) print wanted "=" value }
        ' "$ENV_FILE" > "$temp_file"
        chmod 600 "$temp_file"
        mv "$temp_file" "$ENV_FILE"
        info "已生成 ${name}"
    }

    ensure_secret GATEWAY_MASTER_KEY "sk-"
    ensure_secret REDIS_PASSWORD
    ensure_secret POSTGRES_PASSWORD

    local dashboard_auth
    dashboard_auth="$(awk -F= '$1 == "DASHBOARD_AUTH" {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE" 2>/dev/null || true)"
    dashboard_auth="$(printf '%s' "${dashboard_auth:-local}" | tr -d '[:space:]"'"'"'')"
    case "$dashboard_auth" in
        local) info "仪表盘：本机免登录" ;;
        token) ensure_secret DASHBOARD_TOKEN "dash-"; info "仪表盘：令牌模式" ;;
        accounts) info "仪表盘：账户模式" ;;
        *) die "DASHBOARD_AUTH 只能是 local、token 或 accounts" ;;
    esac
    chmod 600 "$ENV_FILE"

    local postgres_password
    postgres_password="$(awk -F= '$1 == "POSTGRES_PASSWORD" {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE")"
    if [[ ! "$postgres_password" =~ ^[A-Za-z0-9._~-]+$ ]]; then
        die "POSTGRES_PASSWORD 只能包含 URL 安全字符 A-Z a-z 0-9 . _ ~ -"
    fi
}

cmd_start() {
    require_docker
    require_license_or_die
    restore_jiyi_on_fresh_home
    seed_user_data
    ensure_runtime_env
    ensure_jiyi_target
    info "程序代码: ${CODE_DIR}"
    info "用户数据: ${DATA_DIR}"

    [[ -f "${CODE_DIR}/docker-compose.yml" ]] || die "缺少 docker-compose.yml"

    "${COMPOSE[@]}" config --quiet
    COMPOSE_STARTED=true
    if ! "${COMPOSE[@]}" up -d --build --remove-orphans; then
        show_diagnostics
        die "docker compose up 失败"
    fi

    local start_seconds failed_container all_healthy
    start_seconds=$SECONDS
    info "等待健康检查（最长 ${STARTUP_TIMEOUT}s）…"
    while true; do
        all_healthy=true
        failed_container=""
        while read -r name status; do
            [[ -z "${name:-}" ]] && continue
            case "$status" in
                *healthy*) ;;
                *unhealthy*|*exited*|*dead*)
                    failed_container="$name"
                    all_healthy=false
                    break
                    ;;
                *)
                    all_healthy=false
                    ;;
            esac
        done < <("${COMPOSE[@]}" ps --format '{{.Name}} {{.Status}}' 2>/dev/null || true)

        if [[ -n "$failed_container" ]]; then
            printf '\n'
            show_diagnostics
            die "容器启动异常：${failed_container}"
        fi
        if [[ "$all_healthy" == true ]]; then
            # 至少有一个 running 才算
            if "${COMPOSE[@]}" ps --status running 2>/dev/null | grep -q .; then
                printf '\n'
                break
            fi
            all_healthy=false
        fi
        if (( SECONDS - start_seconds >= STARTUP_TIMEOUT )); then
            printf '\n'
            show_diagnostics
            die "等待服务健康超时（${STARTUP_TIMEOUT} 秒）"
        fi
        printf '.'
        sleep 2
    done

    info "服务已启动"
    "${COMPOSE[@]}" ps

    cat <<EOF

访问:
  http://127.0.0.1:4000          中文入口
  http://127.0.0.1:4000/v1       OpenAI API
  http://127.0.0.1:4000/console  专业控制台

数据: ${DATA_DIR}
记忆: $(resolve_jiyi_path)   (运行中自动同步；迁移后执行 ${prog_name:-./run.sh} jiyi load)
EOF
}

# ── jiyi ──────────────────────────────────────────────
resolve_jiyi_path() {
    if [[ -n "${AI_GATEWAY_JIYI:-}" ]]; then
        printf '%s\n' "$AI_GATEWAY_JIYI"
        return
    fi
    # 源码：仓库根 jiyi.txt；安装：用户数据目录内
    if [[ "$SOURCE_LAYOUT" -eq 1 ]]; then
        printf '%s\n' "${REPO_ROOT}/jiyi.txt"
    else
        printf '%s\n' "${DATA_DIR}/jiyi.txt"
    fi
}

jiyi_tool() {
    local jiyi cmd
    jiyi="$(resolve_jiyi_path)"
    cmd="${1:-save}"
    shift || true
    export AI_GATEWAY_HOME="$DATA_DIR"
    export AI_GATEWAY_JIYI="$jiyi"
    local py="${CODE_DIR}/scripts/jiyi_store.py"
    [[ -f "$py" ]] || die "找不到 ${py}"
    python3 "$py" "$cmd" --data-dir "$DATA_DIR" --code-dir "$CODE_DIR" --jiyi "$jiyi" "$@"
}

restore_jiyi_on_fresh_home() {
    local jiyi
    jiyi="$(resolve_jiyi_path)"
    if [[ ! -f "${DATA_DIR}/.env" && -s "$jiyi" ]]; then
        jiyi_tool load >/dev/null
        info "已从 ${jiyi} 自动导入迁移数据"
    fi
}

ensure_jiyi_target() {
    local jiyi
    jiyi="$(resolve_jiyi_path)"
    mkdir -p "$(dirname -- "$jiyi")"
    if [[ ! -s "$jiyi" ]]; then
        jiyi_tool save >/dev/null
        info "已生成迁移凭证 ${jiyi}"
    fi
    chmod 600 "$jiyi"
}

cmd_jiyi() {
    local sub="${1:-save}"
    case "$sub" in
        save|load|path|list) shift || true; jiyi_tool "$sub" "$@" ;;
        help|-h|--help)
            echo "用法: $(basename "$0") jiyi {save|load|path|list}"
            echo "记忆文件: $(resolve_jiyi_path)"
            ;;
        *) die "未知 jiyi 子命令: $sub" ;;
    esac
}

cmd_doctor() {
    AI_GATEWAY_JIYI="$(resolve_jiyi_path)" python3 "${CODE_DIR}/scripts/doctor.py" --data-dir "$DATA_DIR" --code-dir "$CODE_DIR" --live
}

# ── backup/restore（用户数据目录）──────────────────────
default_backup_path() {
    local ts desk
    ts="$(date +%Y%m%d-%H%M%S)"
    desk=""
    if command -v xdg-user-dir >/dev/null 2>&1; then
        desk="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
    fi
    if [[ -n "$desk" && -d "$desk" ]]; then
        printf '%s\n' "${desk}/ai-gateway-matrix-backup-${ts}.tgz"
    else
        printf '%s\n' "${HOME}/ai-gateway-matrix-backup-${ts}.tgz"
    fi
}

cmd_backup() {
    local out out_parent stage
    out="${1:-}"
    [[ -z "$out" ]] && out="$(default_backup_path)"
    out="${out/#\~/$HOME}"
    [[ "$out" != /* ]] && out="$(pwd)/$out"
    out_parent="$(dirname -- "$out")"
    mkdir -p "$out_parent"
    [[ -d "$DATA_DIR" ]] || die "数据目录不存在: $DATA_DIR"
    stage="$(mktemp -d "${TMPDIR:-/tmp}/agm-backup.XXXXXX")"
    if ! cp -al "$DATA_DIR" "${stage}/ai-gateway-matrix" 2>/dev/null; then
        cp -a "$DATA_DIR" "${stage}/ai-gateway-matrix"
    fi
    rm -rf "${stage}/ai-gateway-matrix/data/postgres/pg_stat_tmp" \
        "${stage}/ai-gateway-matrix/data/postgres/postmaster.pid" 2>/dev/null || true
    tar -czf "$out" -C "$stage" ai-gateway-matrix
    rm -rf "$stage"
    chmod 600 "$out" 2>/dev/null || true
    # 同步 jiyi
    jiyi_tool save >/dev/null 2>&1 || true
    info "已备份: $out"
    printf '%s\n' "$out"
}

cmd_restore() {
    local archive tmp top bak
    archive="${1:-}"
    [[ -n "$archive" ]] || die "用法: restore <backup.tgz>"
    archive="${archive/#\~/$HOME}"
    [[ -f "$archive" ]] || die "不存在: $archive"
    if docker info >/dev/null 2>&1; then
        setup_compose 2>/dev/null || true
        "${COMPOSE[@]}" down 2>/dev/null || true
    fi
    tmp="$(mktemp -d "${TMPDIR:-/tmp}/agm-restore.XXXXXX")"
    trap 'rm -rf "'"$tmp"'"' EXIT
    tar -xzf "$archive" -C "$tmp"
    if [[ -d "${tmp}/ai-gateway-matrix" ]]; then
        top="${tmp}/ai-gateway-matrix"
    elif [[ -f "${tmp}/.env" || -f "${tmp}/config.yaml" ]]; then
        top="$tmp"
    else
        top="$(find "$tmp" -mindepth 1 -maxdepth 1 -type d | head -1 || true)"
        [[ -n "$top" ]] || die "备份结构无法识别"
    fi
    if [[ -e "$DATA_DIR" ]]; then
        bak="${DATA_DIR}.pre-restore-$(date +%Y%m%d-%H%M%S)"
        mv "$DATA_DIR" "$bak"
        info "原数据 → $bak"
    fi
    mkdir -p "$(dirname "$DATA_DIR")" "$DATA_DIR"
    tar -C "$top" -cf - . | tar -C "$DATA_DIR" -xf -
    chmod 700 "$DATA_DIR" 2>/dev/null || true
    [[ -f "${DATA_DIR}/.env" ]] && chmod 600 "${DATA_DIR}/.env"
    trap - EXIT
    rm -rf "$tmp"
    jiyi_tool save >/dev/null 2>&1 || true
    info "已恢复 → $DATA_DIR"
}

# ── 参数 ──────────────────────────────────────────────
case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    app|flutter|start|stop|restart|status|doctor|logs|license|home|version|backup|restore|jiyi)
        COMMAND="$1"; shift ;;
    "") COMMAND="app" ;;
    *) usage >&2; exit 2 ;;
esac

DATA_DIR="$(resolve_data_dir)"
if [[ "$DATA_DIR" != /* ]]; then
    DATA_DIR="$(cd -- "$(dirname -- "$DATA_DIR")" 2>/dev/null && pwd)/$(basename -- "$DATA_DIR")" || DATA_DIR="$(resolve_data_dir)"
fi

trap 'die "命令失败（脚本第 ${LINENO} 行）"' ERR

case "$COMMAND" in
    home) cmd_home ;;
    version) cmd_version ;;
    jiyi) printf '%b\n' "${BOLD}记忆文件 jiyi.txt${RESET}"; cmd_jiyi "$@" ;;
    backup) printf '%b\n' "${BOLD}备份${RESET}"; cmd_backup "$@" ;;
    restore) printf '%b\n' "${BOLD}恢复${RESET}"; cmd_restore "$@" ;;
    app) printf '%b\n' "${BOLD}Appica 桌面控制台${RESET}"; cmd_app "$@" ;;
    flutter) printf '%b\n' "${BOLD}Flutter 兼容界面${RESET}"; cmd_flutter "$@" ;;
    license) cmd_license "$@" ;;
    status) cmd_status ;;
    doctor) printf '%b\n' "${BOLD}系统自检${RESET}"; cmd_doctor ;;
    logs) cmd_logs "$@" ;;
    stop) printf '%b\n' "${BOLD}停止${RESET}"; cmd_stop ;;
    restart) cmd_stop || true; cmd_start ;;
    start) printf '%b\n' "${BOLD}启动${RESET}"; cmd_start ;;
esac
