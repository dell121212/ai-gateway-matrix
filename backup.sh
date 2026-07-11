#!/usr/bin/env bash

set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEST="${1:-${ROOT}/backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="${DEST}/${STAMP}"

command -v docker >/dev/null 2>&1 || { echo "未找到 Docker" >&2; exit 1; }
mkdir -p "$TARGET"
chmod 700 "$DEST" "$TARGET"

cd "$ROOT"
docker compose exec -T postgres pg_dump -U litellm -d litellm -Fc > "${TARGET}/postgres.dump"
docker compose exec -T redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli --rdb -' > "${TARGET}/redis.rdb"
cp config.yaml provider_manifest.yaml "${TARGET}/"
chmod 600 "${TARGET}"/*

printf '%s\n' \
  '此备份包含 PostgreSQL、Redis、config.yaml 和 provider_manifest.yaml。' \
  '为避免生成另一份明文密钥，.env 故意没有进入备份；请用密码管理器单独备份 API Key。' \
  > "${TARGET}/README.txt"
chmod 600 "${TARGET}/README.txt"
echo "备份已写入: ${TARGET}"
