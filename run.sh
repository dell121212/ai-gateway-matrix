#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"
ENV_EXAMPLE="${PROJECT_DIR}/.env.example"
STARTUP_TIMEOUT="${STARTUP_TIMEOUT:-180}"

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

启动 AI Gateway Matrix 的网关、仪表盘、Redis 和 PostgreSQL。
可通过 STARTUP_TIMEOUT 环境变量调整健康检查等待时间（默认 180 秒）。
EOF
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

command -v docker >/dev/null 2>&1 || die "未找到 Docker，请先安装 Docker Engine 或 Docker Desktop"

if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
else
    die "未找到 Docker Compose，请安装 Docker Compose v2"
fi

docker info >/dev/null 2>&1 || die "无法连接 Docker，请确认 Docker 已启动且当前用户有访问权限"
info "Docker 与 Compose 可用"

[[ -f "$ENV_EXAMPLE" ]] || die "缺少 .env.example"

umask 077
if [[ ! -f "$ENV_FILE" ]]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    info "已根据 .env.example 创建 .env"
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

ensure_secret DASHBOARD_TOKEN "dash-"
ensure_secret REDIS_PASSWORD
ensure_secret POSTGRES_PASSWORD
chmod 600 "$ENV_FILE"

# DATABASE_URL 直接嵌入 PostgreSQL 密码；限制为 URL 安全字符，避免用户手工
# 填入 @、:、/ 等字符后 Compose 能解析但数据库连接串语义被截断。
postgres_password="$(awk -F= '$1 == "POSTGRES_PASSWORD" {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE")"
if [[ ! "$postgres_password" =~ ^[A-Za-z0-9._~-]+$ ]]; then
    die "POSTGRES_PASSWORD 只能包含 URL 安全字符 A-Z a-z 0-9 . _ ~ -；可留空让 ./run.sh 自动生成"
fi
unset postgres_password

provider_key_count="$(awk -F= '
    /^[[:space:]]*[A-Z0-9_]+[[:space:]]*=/ {
        name = $1
        gsub(/[[:space:]]/, "", name)
        value = $0
        sub(/^[^=]*=/, "", value)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
        if (name != "GATEWAY_MASTER_KEY" && name != "DASHBOARD_TOKEN" &&
            name != "REDIS_PASSWORD" && name != "POSTGRES_PASSWORD" && value != "" &&
            (name ~ /API_KEY$/ || name ~ /TOKEN$/ || name ~ /KEY_[0-9]+$/)) {
            count++
        }
    }
    END { print count + 0 }
' "$ENV_FILE")"

if [[ "$provider_key_count" -eq 0 ]]; then
    warn "尚未配置上游模型 API Key；服务会启动，但调用模型前请在 .env 或仪表盘中填写至少一个 Key"
else
    info "检测到 ${provider_key_count} 个已配置的上游凭据"
fi

if command -v python3 >/dev/null 2>&1; then
    python3 -m scripts.validate_config
    info "项目严格配置校验通过"
else
    warn "未找到 python3，跳过 scripts.validate_config（Docker Compose 校验仍会执行）"
fi

"${COMPOSE[@]}" config --quiet
info "Docker Compose 配置校验通过"

printf '\n正在构建并启动服务…\n'
"${COMPOSE[@]}" up -d --build

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
        state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)"
        case "$state" in
            healthy)
                ;;
            unhealthy|exited|dead)
                failed_container="$container ($state)"
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
        "${COMPOSE[@]}" ps
        "${COMPOSE[@]}" logs --tail=100
        die "容器启动异常：${failed_container}"
    fi

    if [[ "$all_healthy" == true ]]; then
        printf '\n'
        break
    fi

    if (( SECONDS - start_seconds >= STARTUP_TIMEOUT )); then
        printf '\n'
        "${COMPOSE[@]}" ps
        die "等待服务健康超时（${STARTUP_TIMEOUT} 秒），可用 docker compose logs 查看日志"
    fi

    printf '.'
    sleep 2
done

info "所有服务已启动并通过健康检查"
"${COMPOSE[@]}" ps

cat <<'EOF'

访问地址：
  API 网关: http://127.0.0.1:4000
  仪表盘: http://127.0.0.1:8080

  仪表盘首次访问会询问 DASHBOARD_TOKEN，请从 .env 中查看。

常用命令：
  docker compose logs -f
  docker compose down
EOF
