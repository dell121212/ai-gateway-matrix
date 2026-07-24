# 打包说明

## 构建 deb

```bash
# 在仓库根
./packaging/build-deb.sh
```

产物：`dist/ai-gateway-matrix_<VERSION>_all.deb`（`VERSION` 来自仓库根 `VERSION` 文件）。

依赖：`dpkg-deb`、`fakeroot`（可选）、`rsync`、本机 bash。

## 安装与数据目录

```bash
sudo dpkg -i dist/ai-gateway-matrix_*.deb
ai-gateway-matrix app       # 桌面应用窗口（应用菜单亦可）
ai-gateway-matrix start     # 仅后端
```

| 位置 | 内容 |
|------|------|
| `/usr/share/ai-gateway-matrix` | 只读程序（gateway/dashboard/desktop/licensing/scripts/compose） |
| `/usr/bin/ai-gateway-matrix` | 入口命令 |
| `/usr/share/applications/…desktop` | 应用菜单项 |
| `~/.config/ai-gateway-matrix` | **用户全部可迁移数据**（默认，含 license/） |

正式 deb 要求已有 `licensing/public/ai-gateway.pub`（`bash licensing/init_issuer.sh`）。

覆盖数据目录：

```bash
export AI_GATEWAY_HOME="$HOME/Documents/ai-gateway-matrix"
ai-gateway-matrix app
```

### 用户数据目录结构

```text
$AI_GATEWAY_HOME/
  .env                      # 上游 Key + 内部密钥（0600）
  config.yaml               # 渠道与路由
  provider_manifest.yaml    # 能力/敏感策略
  state/                    # 仪表盘状态、客户端 Key 登记等
  data/redis/               # Redis 持久化
  data/postgres/            # LiteLLM 元数据库
```

迁移到另一台 Linux：

1. `ai-gateway-matrix stop`
2. 打包整个 `$AI_GATEWAY_HOME`
3. 目标机安装相同（或兼容）版本 deb
4. 解压到相同路径，或 `export AI_GATEWAY_HOME=...`
5. `ai-gateway-matrix start`

卸载 `dpkg -r` / `dpkg -P` **不会**删除用户数据目录。

## 设计要点

- 社区/XDG 惯例：程序装系统目录，配置与密钥不进 deb 的 conffiles。
- Compose 用 `AI_GATEWAY_CODE` 挂只读代码，用 `--project-directory` 指向用户目录的 `.env`/`config`/`state`/`data`。
- Redis/Postgres 改为 bind mount，随用户目录一起迁移（不再用匿名 Docker volume）。
