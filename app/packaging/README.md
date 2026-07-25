# 打包说明

## 构建 deb

```bash
# 在仓库根（建议先构建前端）
(cd dashboard/frontend && npm ci && npm run build)  # 可选但推荐
./packaging/build-deb.sh
```

产物：`dist/ai-gateway-matrix_<VERSION>_all.deb`（`VERSION` 来自仓库根 `VERSION` 文件）。

| 公钥 | 包类型 |
|------|--------|
| 无 `licensing/public/ai-gateway.pub` | **个人版**：无授权闸，装完即可 start（默认**不**走保护混淆） |
| 有公钥 | **正式版**：未激活不启动 + **默认启用保护管线** |

知识产权保护（对齐 AUTO-R `packaging/protect`）：

```bash
# 仅生成受保护载荷
bash packaging/protect/build_protected_package.sh

# 强制保护进 deb（个人版也可）
AGM_PROTECT=1 ./packaging/build-deb.sh

# 解包预演
bash tests/verify_deb_package.sh dist/ai-gateway-matrix_*.deb
```

详见 `packaging/protect/README.md`。`watermark_rules.json` 仅授权人持有，**永不进 deb**。

依赖：`dpkg-deb`、`fakeroot`（可选）、`rsync`、本机 bash。

## 安装与数据目录

```bash
sudo dpkg -i dist/ai-gateway-matrix_*.deb
sudo apt-get install -f   # 若提示依赖不足
ai-gateway-matrix app       # 桌面应用窗口（应用菜单亦可）
ai-gateway-matrix start     # 仅后端
```

| 位置 | 内容 |
|------|------|
| `/usr/share/ai-gateway-matrix` | 只读程序（gateway/dashboard/desktop/licensing/scripts/compose） |
| `/usr/bin/ai-gateway-matrix` | 入口命令 |
| `/usr/share/applications/…desktop` | 应用菜单项 |
| `~/.config/ai-gateway-matrix` | **用户全部可迁移数据**（默认，含 license/） |

### 升级 = 自动保留全部 Key 与设置

```bash
ai-gateway-matrix backup                 # 建议
sudo dpkg -i dist/ai-gateway-matrix_新版本_all.deb
ai-gateway-matrix start
```

deb **只替换** `/usr/share/...` 程序树；**永不写入**用户数据目录。

### 一键备份 / 恢复（单个文件）

```bash
ai-gateway-matrix backup ~/桌面/agm.tgz
ai-gateway-matrix restore ~/桌面/agm.tgz
ai-gateway-matrix start
```

覆盖数据目录：

```bash
export AI_GATEWAY_HOME="$HOME/Documents/ai-gateway-matrix"
ai-gateway-matrix app
```

详见 `docs/PORTABLE_DATA.md`。

### 用户数据目录结构

```text
$AI_GATEWAY_HOME/
  .env                      # 上游 Key + 内部密钥（0600）
  config.yaml               # 渠道与路由
  provider_manifest.yaml    # 能力/敏感策略
  state/                    # 仪表盘状态、客户端 Key 登记等
  license/                  # 离线授权
  data/redis/               # Redis 持久化
  data/postgres/            # LiteLLM + 业务库
  PORTABLE.txt
```

卸载 `dpkg -r` / `dpkg -P` **不会**删除用户数据目录。

## 设计要点

- 社区/XDG 惯例：程序装系统目录，配置与密钥不进 deb 的 conffiles。
- Compose 用 `AI_GATEWAY_CODE` 挂只读代码，用 `--project-directory` 指向用户目录的 `.env`/`config`/`state`/`data`。
- Redis/Postgres 改为 bind mount，随用户目录一起迁移（不再用匿名 Docker volume）。
