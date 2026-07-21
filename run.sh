#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"
ENV_EXAMPLE="${PROJECT_DIR}/.env.example"
STARTUP_TIMEOUT="${STARTUP_TIMEOUT:-180}"
COMPOSE_STARTED=false

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

usage() {
    cat <<'EOF'
用法: ./run.sh

首次运行会创建本地配置、生成内部密钥、下载/构建镜像，然后启动
AI Gateway Matrix 的网关、仪表盘、Redis 和 PostgreSQL。
后续可重复执行，已有的 .env 和数据卷不会被覆盖。

唯一前置条件：Docker Engine / Docker Desktop（包含 Docker Compose）。
可通过 STARTUP_TIMEOUT 环境变量调整健康检查等待时间（默认 180 秒）。
EOF
}

show_docker_install_help() {
    cat >&2 <<'EOF'
请先安装并启动 Docker：
  Linux:   https://docs.docker.com/engine/install/
  macOS:   https://docs.docker.com/desktop/setup/install/mac-install/
  Windows: https://docs.docker.com/desktop/setup/install/windows-install/
安装完成后重新执行 ./run.sh。
EOF
}

show_diagnostics() {
    if [[ "$COMPOSE_STARTED" == true ]]; then
        printf '\n%b\n' "${YELLOW}最近的容器状态与日志：${RESET}" >&2
        "${COMPOSE[@]}" ps >&2 || true
        "${COMPOSE[@]}" logs --tail=100 >&2 || true
    fi
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

if [[ $# -gt 0 ]]; then
    usage >&2
    exit 2
fi

trap 'die "启动失败（脚本第 ${LINENO} 行）"' ERR

cd "$PROJECT_DIR"

if [[ ! "$STARTUP_TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then
    die "STARTUP_TIMEOUT 必须是大于 0 的整数"
fi

printf '%b\n' "${BOLD}AI Gateway Matrix 一键启动${RESET}"

if ! command -v docker >/dev/null 2>&1; then
    show_docker_install_help
    die "未找到 Docker"
fi

if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
else
    die "未找到 Docker Compose，请安装 Docker Compose v2"
fi

if ! docker info >/dev/null 2>&1; then
    die "无法连接 Docker；请启动 Docker，并确认当前用户可执行 docker info"
fi
info "Docker 与 Compose 可用"

[[ -f "$ENV_EXAMPLE" ]] || die "缺少 .env.example"
[[ -f "${PROJECT_DIR}/docker-compose.yml" ]] || die "缺少 docker-compose.yml"
[[ -f "${PROJECT_DIR}/config.yaml" ]] || die "缺少 config.yaml"

# 空目录在下载源码或部分打包方式中可能丢失。提前创建，避免 Docker
# 自动创建为 root 所有后，provider-monitor 无法写入审计报告。
mkdir -p "${PROJECT_DIR}/state"
[[ -w "${PROJECT_DIR}/state" ]] || die "state 目录不可写：${PROJECT_DIR}/state"
# provider-monitor 以当前宿主用户身份写绑定挂载的 ./state。不能只依赖
# 容器里的 root：该服务会 drop 掉全部 capabilities，因而无权绕过目录的
# 所有者/组权限。导出给 Compose 的 user 字段，不写入 .env，也不需要 sudo。
export HOST_UID="$(id -u)"
export HOST_GID="$(id -g)"
info "运行目录已就绪"

umask 077
if [[ ! -f "$ENV_FILE" ]]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    info "已根据 .env.example 创建 .env"
else
    # 升级时只补充新版模板中新增的变量，绝不覆盖用户已有值。
    # 这使旧版 .env 可以继续用，也避免为备份而复制一份明文密钥。
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
        printf '\n# --- ./run.sh 从新版 .env.example 补充 ---\n%s\n' "$missing_env_lines" >> "$ENV_FILE"
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

# DATABASE_URL 直接嵌入 PostgreSQL 密码；限制为 URL 安全字符，避免用户手工
# 填入 @、:、/ 等字符后 Compose 能解析但数据库连接串语义被截断。
postgres_password="$(awk -F= '$1 == "POSTGRES_PASSWORD" {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE")"
if [[ ! "$postgres_password" =~ ^[A-Za-z0-9._~-]+$ ]]; then
    die "POSTGRES_PASSWORD 只能包含 URL 安全字符 A-Z a-z 0-9 . _ ~ -；可留空让 ./run.sh 自动生成"
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
' "$PROJECT_DIR/config.yaml" "$ENV_FILE")"

if [[ "$provider_key_count" -eq 0 ]]; then
    warn "尚未配置上游模型 API Key；服务会启动，但调用模型前请在仪表盘填写至少一个渠道 Key"
else
    info "检测到 ${provider_key_count} 个已配置的上游凭据"
fi

if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' >/dev/null 2>&1; then
    python3 -m scripts.validate_config
    info "项目严格配置校验通过"
else
    warn "本机未安装 Python 3 + PyYAML，跳过可选的严格配置校验（不影响 Docker 安装）"
fi

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

cat <<'EOF'

访问地址：
  中文统一入口: http://127.0.0.1:4000
  OpenAI API Base: http://127.0.0.1:4000/v1
  兼容管理入口: http://127.0.0.1:8080

  个人模式仅监听本机，打开中文控制台即可管理，无需再次登录。

常用命令：
  docker compose logs -f
  docker compose down

改代码后（gateway/*.py 或 dashboard/*.py）：
  docker compose restart ai-gateway-matrix dashboard
  （静态 HTML 热更新无需重启；Python 不热重载）

闭环说明：
  智脑分档 → 强制非流式 → 质检 → 不合格换家（router num_retries）
EOF
