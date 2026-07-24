#!/usr/bin/env bash
# AI Gateway Matrix 启动器
# - 源码模式：配置与代码同目录（仓库根）
# - 安装模式（deb）：代码在 /usr/share/...，用户数据在 AI_GATEWAY_HOME

set -Eeuo pipefail

CODE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
STARTUP_TIMEOUT="${STARTUP_TIMEOUT:-180}"
COMPOSE_STARTED=false
COMMAND="start"

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

info() {
    printf '%b\n' "${GREEN}✓${RESET} $*"
}

warn() {
    printf '%b\n' "${YELLOW}⚠${RESET} $*"
}

die() {
    printf '%b\n' "${RED}✗${RESET} $*" >&2
    exit 1
}

# 安装包在 CODE_DIR 放 .installed 标记；也兼容路径落在 /usr/share。
is_installed_layout() {
    [[ -f "${CODE_DIR}/.installed" ]] || [[ "$CODE_DIR" == /usr/share/ai-gateway-matrix ]]
}

default_data_dir() {
    if is_installed_layout; then
        printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/ai-gateway-matrix"
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
  app       打开桌面应用窗口（推荐；自动 start 后端）
  start     仅启动网关服务（无窗口；首次会初始化用户数据目录）
  stop      停止全部容器
  restart   重启服务
  status    查看容器状态
  logs      跟踪日志（可附 docker compose logs 参数）
  license   授权：request|status|import|ensure|device-id|…
  home      打印可迁移的用户数据目录路径
  version   打印版本

授权（B 端离线，借鉴 AUTO-R）:
  有公钥时：未激活不启动服务。
  开发：无 licensing/public/ai-gateway.pub 时跳过；或 AI_GATEWAY_LICENSE_BYPASS=1

用户数据目录（可整体拷贝迁移）:
  默认: \${XDG_CONFIG_HOME:-\$HOME/.config}/ai-gateway-matrix
  覆盖: export AI_GATEWAY_HOME=/path/to/dir

目录内包含:
  .env                  上游 Key 与内部密钥（0600）
  config.yaml           渠道/路由配置
  provider_manifest.yaml
  state/                仪表盘状态、客户端 Key 登记等
  data/redis|postgres/  持久化数据（随目录一起迁移）

桌面窗口依赖（Debian/Ubuntu）:
  sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1

唯一前置条件：Docker Engine（含 Docker Compose v2）。
STARTUP_TIMEOUT 可调整健康检查等待秒数（默认 180）。
EOF
}

show_docker_install_help() {
    cat >&2 <<'EOF'
请先安装并启动 Docker：
  Linux:   https://docs.docker.com/engine/install/
  macOS:   https://docs.docker.com/desktop/setup/install/mac-install/
  Windows: https://docs.docker.com/desktop/setup/install/windows-install/
安装完成后重新执行本命令。
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

    # 兼容旧开发布局：若仍使用仓库根下的 state，无需额外动作。
    [[ -w "${DATA_DIR}/state" ]] || die "state 目录不可写：${DATA_DIR}/state"
}

setup_compose() {
    export AI_GATEWAY_CODE="$CODE_DIR"
    export AI_GATEWAY_HOME="$DATA_DIR"
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

cmd_home() {
    printf '%s\n' "$DATA_DIR"
}

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
    "$tool" "$@"
}

# 启动闸：未激活则拒绝 start/app（开发模式见 license ensure 逻辑）
require_license_or_die() {
    local rc=0
    license_tool ensure || rc=$?
    if [[ "$rc" -eq 0 ]]; then
        return 0
    fi
    if [[ "$rc" -eq 10 ]]; then
        # 生成激活页路径供桌面壳使用
        license_tool activation-html >/dev/null 2>&1 || true
        die "未激活：已打印设备申请码。请将 .lic 放到桌面后重试，或执行: $(basename -- "$0") license import <file.lic>"
    fi
    die "授权检查失败（退出码 ${rc}）"
}

cmd_license() {
    if [[ $# -eq 0 ]]; then
        license_tool status
        return
    fi
    license_tool "$@"
}

cmd_app() {
    # 桌面壳：先过授权闸；未激活时打开激活页而不是启动 Docker
    export AI_GATEWAY_HOME="$DATA_DIR"
    export PYTHONPATH="${CODE_DIR}${PYTHONPATH:+:$PYTHONPATH}"
    local rc=0
    license_tool ensure || rc=$?
    if [[ "$rc" -eq 10 ]]; then
        local act
        act="$(license_tool activation-html)"
        info "未激活：打开激活页（不启动服务）"
        if command -v python3 >/dev/null 2>&1; then
            exec python3 -m desktop.app --activation-file "$act" --no-start "$@"
        fi
        die "未激活。申请码请运行: $(basename -- "$0") license request"
    fi
    if [[ "$rc" -ne 0 ]]; then
        die "授权检查失败（退出码 ${rc}）"
    fi
    if command -v python3 >/dev/null 2>&1; then
        exec python3 -m desktop.app "$@"
    fi
    die "需要 python3 才能打开桌面应用窗口"
}

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
    info "已停止（用户数据仍保留在 ${DATA_DIR}）"
}

cmd_start() {
    # 用户数据目录先创建，license 身份文件写在 DATA_DIR/license/
    mkdir -p "${DATA_DIR}/state" "${DATA_DIR}/license"
    require_license_or_die

    require_docker
    info "Docker 与 Compose 可用"

    local env_example
    env_example="$(template_path .env.example)" || die "缺少 .env.example"
    ENV_EXAMPLE="$env_example"
    ENV_FILE="${DATA_DIR}/.env"

    [[ -f "${CODE_DIR}/docker-compose.yml" ]] || die "缺少 docker-compose.yml"
    seed_user_data
    info "用户数据目录: ${DATA_DIR}"
    info "程序代码目录: ${CODE_DIR}"

    umask 077
    # 升级时只补充新版模板中新增的变量，绝不覆盖用户已有值。
    if [[ -f "$ENV_FILE" ]]; then
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
        ' "$ENV_FILE" "$ENV_EXAMPLE")"
        if [[ -n "$missing_env_lines" ]]; then
            missing_env_count="$(printf '%s\n' "$missing_env_lines" | awk 'END { print NR }')"
            printf '\n# --- run.sh 从新版 .env.example 补充 ---\n%s\n' "$missing_env_lines" >> "$ENV_FILE"
            info "已向 .env 补充 ${missing_env_count} 个新配置项（已有值未覆盖）"
            unset missing_env_count
        fi
        unset missing_env_lines
    fi

    current_master_key="$({
        awk -F= '
            /^[[:space:]]*GATEWAY_MASTER_KEY[[:space:]]*=/ {
                value = $0
                sub(/^[^=]*=/, "", value)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
                print value
                exit
            }
        ' "$ENV_FILE"
    } || true)"

    if [[ -z "$current_master_key" ]]; then
        if command -v openssl >/dev/null 2>&1; then
            generated_master_key="sk-$(openssl rand -hex 24)"
        elif command -v python3 >/dev/null 2>&1; then
            generated_master_key="$(python3 -c 'import secrets; print("sk-" + secrets.token_hex(24))')"
        else
            die "无法生成网关密钥：需要 openssl 或 python3"
        fi

        temp_env="$(mktemp "${ENV_FILE}.tmp.XXXXXX")"
        awk -v key="$generated_master_key" '
            BEGIN { found = 0 }
            /^[[:space:]]*GATEWAY_MASTER_KEY[[:space:]]*=/ {
                print "GATEWAY_MASTER_KEY=" key
                found = 1
                next
            }
            { print }
            END {
                if (!found) {
                    print "GATEWAY_MASTER_KEY=" key
                }
            }
        ' "$ENV_FILE" > "$temp_env"
        chmod 600 "$temp_env"
        mv "$temp_env" "$ENV_FILE"
        unset generated_master_key
        info "已生成 GATEWAY_MASTER_KEY 并保存到 .env（不在终端回显）"
    else
        info ".env 中已配置 GATEWAY_MASTER_KEY"
    fi
    unset current_master_key

    ensure_secret() {
        local name="$1"
        local prefix="${2:-}"
        local current generated temp_file
        current="$(awk -F= -v wanted="$name" '
            $1 == wanted {
                value = $0
                sub(/^[^=]*=/, "", value)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
                print value
                exit
            }
        ' "$ENV_FILE")"
        if [[ -n "$current" ]]; then
            info ".env 中已配置 ${name}"
            return
        fi
        if command -v openssl >/dev/null 2>&1; then
            generated="${prefix}$(openssl rand -hex 24)"
        elif command -v python3 >/dev/null 2>&1; then
            generated="${prefix}$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
        else
            die "无法生成 ${name}：需要 openssl 或 python3"
        fi
        temp_file="$(mktemp "${ENV_FILE}.tmp.XXXXXX")"
        awk -F= -v wanted="$name" -v value="$generated" '
            BEGIN { found = 0 }
            $1 == wanted { print wanted "=" value; found = 1; next }
            { print }
            END { if (!found) print wanted "=" value }
        ' "$ENV_FILE" > "$temp_file"
        chmod 600 "$temp_file"
        mv "$temp_file" "$ENV_FILE"
        unset generated current
        info "已生成 ${name} 并安全保存到 .env"
    }

    ensure_secret REDIS_PASSWORD
    ensure_secret POSTGRES_PASSWORD

    dashboard_auth="$(awk -F= '$1 == "DASHBOARD_AUTH" {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE")"
    dashboard_auth="${dashboard_auth:-local}"
    case "$dashboard_auth" in
        local)
            info "仪表盘使用仅本机免登录模式"
            ;;
        token)
            ensure_secret DASHBOARD_TOKEN "dash-"
            info "仪表盘使用令牌保护模式"
            ;;
        *)
            die "DASHBOARD_AUTH 只能是 local 或 token"
            ;;
    esac
    unset dashboard_auth
    chmod 600 "$ENV_FILE"

    # DATABASE_URL 直接嵌入 PostgreSQL 密码；限制为 URL 安全字符。
    postgres_password="$(awk -F= '$1 == "POSTGRES_PASSWORD" {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE")"
    if [[ ! "$postgres_password" =~ ^[A-Za-z0-9._~-]+$ ]]; then
        die "POSTGRES_PASSWORD 只能包含 URL 安全字符 A-Z a-z 0-9 . _ ~ -；可留空让启动脚本自动生成"
    fi
    unset postgres_password

    provider_key_count="$(awk '
        FNR == NR {
            line = $0
            if (line ~ /api_key:[[:space:]]*os\.environ\//) {
                sub(/^.*os\.environ\//, "", line)
                sub(/[[:space:]#].*$/, "", line)
                refs[line] = 1
            }
            next
        }
        /^[[:space:]]*[A-Z0-9_]+[[:space:]]*=/ {
            name = $0
            sub(/=.*/, "", name)
            gsub(/[[:space:]]/, "", name)
            value = $0
            sub(/^[^=]*=/, "", value)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
            if (name in refs && value != "" && value !~ /^dummy-/) {
                count++
            }
        }
        END { print count + 0 }
    ' "${DATA_DIR}/config.yaml" "$ENV_FILE")"

    if [[ "$provider_key_count" -eq 0 ]]; then
        warn "尚未配置上游模型 API Key；服务会启动，但调用模型前请在仪表盘填写至少一个渠道 Key"
    else
        info "检测到 ${provider_key_count} 个已配置的上游凭据"
    fi

    if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' >/dev/null 2>&1; then
        if (
            AI_GATEWAY_HOME="$DATA_DIR" \
            PYTHONPATH="${CODE_DIR}${PYTHONPATH:+:$PYTHONPATH}" \
                python3 -m scripts.validate_config
        ); then
            info "项目严格配置校验通过"
        else
            warn "严格配置校验未通过；请检查 ${DATA_DIR}/config.yaml"
        fi
    else
        warn "本机未安装 Python 3 + PyYAML，跳过可选的严格配置校验（不影响 Docker 安装）"
    fi

    # Compose 会从 --project-directory 下的 .env 做 ${VAR} 插值；勿 source 密钥文件。
    "${COMPOSE[@]}" config --quiet
    info "Docker Compose 配置校验通过"

    printf '\n正在构建并启动服务…\n'
    COMPOSE_STARTED=true
    if ! "${COMPOSE[@]}" up -d --build --remove-orphans; then
        show_diagnostics
        die "Docker Compose 构建或启动失败"
    fi

    containers=(
        ai-gateway-matrix-redis
        ai-gateway-matrix-postgres
        ai-gateway-matrix
        ai-gateway-matrix-dashboard
        ai-gateway-matrix-provider-monitor
    )

    printf '正在等待服务健康检查'
    start_seconds=$SECONDS
    while true; do
        all_healthy=true
        failed_container=''

        for container in "${containers[@]}"; do
            inspection="$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container" 2>/dev/null || true)"
            if [[ -z "$inspection" ]]; then
                failed_container="$container (未找到)"
                all_healthy=false
                break
            fi
            container_state="${inspection%% *}"
            health_state="${inspection#* }"
            case "$container_state:$health_state" in
                running:healthy|running:none)
                    ;;
                running:unhealthy|restarting:*|exited:*|dead:*|paused:*|removing:*)
                    failed_container="$container ($container_state/$health_state)"
                    all_healthy=false
                    break
                    ;;
                *)
                    all_healthy=false
                    ;;
            esac
        done

        if [[ -n "$failed_container" ]]; then
            printf '\n'
            show_diagnostics
            die "容器启动异常：${failed_container}"
        fi

        if [[ "$all_healthy" == true ]]; then
            printf '\n'
            break
        fi

        if (( SECONDS - start_seconds >= STARTUP_TIMEOUT )); then
            printf '\n'
            show_diagnostics
            die "等待服务健康超时（${STARTUP_TIMEOUT} 秒）"
        fi

        printf '.'
        sleep 2
    done

    info "所有服务已启动并通过健康检查"
    "${COMPOSE[@]}" ps

    cat <<EOF

访问地址：
  中文统一入口: http://127.0.0.1:4000
  OpenAI API Base: http://127.0.0.1:4000/v1
  兼容管理入口: http://127.0.0.1:8080

  个人模式仅监听本机，打开中文控制台即可管理，无需再次登录。

可迁移数据目录（Key / 配置 / state / DB）：
  ${DATA_DIR}
  迁移：停服后打包整个目录，到新机器解压并设置 AI_GATEWAY_HOME 后 start。

常用命令：
  $(basename -- "$0") status
  $(basename -- "$0") logs -f
  $(basename -- "$0") stop
  $(basename -- "$0") home
EOF
}

# ── 参数解析 ──────────────────────────────────────────────
case "${1:-}" in
    -h|--help)
        usage
        exit 0
        ;;
    app|start|stop|restart|status|logs|license|home|version)
        COMMAND="$1"
        shift
        ;;
    "")
        # CLI 默认只启动服务；桌面图标 / 菜单项显式用 app
        COMMAND="start"
        ;;
    -*)
        usage >&2
        exit 2
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

if [[ ! "$STARTUP_TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then
    die "STARTUP_TIMEOUT 必须是大于 0 的整数"
fi

DATA_DIR="$(resolve_data_dir)"
# 展开为绝对路径（目录可不存在，home/version 仍可用）
if [[ "$DATA_DIR" != /* ]]; then
    DATA_DIR="$(cd -- "$(dirname -- "$DATA_DIR")" 2>/dev/null && pwd)/$(basename -- "$DATA_DIR")" || DATA_DIR="$(resolve_data_dir)"
fi

trap 'die "命令失败（脚本第 ${LINENO} 行）"' ERR

case "$COMMAND" in
    home)
        cmd_home
        ;;
    version)
        cmd_version
        ;;
    app)
        printf '%b\n' "${BOLD}AI Gateway Matrix 桌面应用${RESET}"
        cmd_app "$@"
        ;;
    license)
        cmd_license "$@"
        ;;
    status)
        printf '%b\n' "${BOLD}AI Gateway Matrix${RESET}"
        cmd_status
        ;;
    logs)
        cmd_logs "$@"
        ;;
    stop)
        printf '%b\n' "${BOLD}AI Gateway Matrix 停止${RESET}"
        cmd_stop
        ;;
    restart)
        printf '%b\n' "${BOLD}AI Gateway Matrix 重启${RESET}"
        cmd_stop || true
        printf '%b\n' "${BOLD}AI Gateway Matrix 一键启动${RESET}"
        cmd_start
        ;;
    start)
        printf '%b\n' "${BOLD}AI Gateway Matrix 一键启动${RESET}"
        cmd_start
        ;;
esac
