# 可迁移用户数据与 deb 升级

## 核心约定

| 位置 | 内容 | 升级 deb 时 |
|------|------|-------------|
| `/usr/share/ai-gateway-matrix` | 程序代码（只读） | **被新 deb 替换** |
| `~/.config/ai-gateway-matrix` | 全部用户数据 | **原样保留** |

因此：**升级 / 重装 deb = 只换程序，设置与 Key 全部保留。**

## 用户数据目录（一个目录 = 全部）

```text
$AI_GATEWAY_HOME/          # 默认 ~/.config/ai-gateway-matrix
  .env                     # 上游 API Key、GATEWAY/REDIS/POSTGRES 密钥
  config.yaml              # 渠道、路由、优先级
  provider_manifest.yaml
  state/                   # 客户端 Key 元数据、UI、业务状态等
  license/                 # 离线授权（若启用）
  data/redis/
  data/postgres/           # LiteLLM 元数据 + private_api 业务表
  PORTABLE.txt             # 本说明副本
```

覆盖路径：

```bash
export AI_GATEWAY_HOME=/path/to/my-agm-data
ai-gateway-matrix start
```

## 记忆文件 jiyi.txt（单文件保留全部设置与 Key）

源码模式默认：`仓库根/jiyi.txt`（本机即 `/home/chenkai/文档/api/jiyi.txt`）。  
安装模式默认：`$AI_GATEWAY_HOME/jiyi.txt`。  
覆盖路径：`export AI_GATEWAY_JIYI=/path/to/jiyi.txt`。

```bash
./run.sh jiyi save    # 把 .env、config、state… 全部写入 jiyi.txt
./run.sh jiyi load    # 从 jiyi.txt 恢复
./run.sh jiyi path
./run.sh jiyi list
```

`start` 成功后会自动 `jiyi save` 一次。  
`jiyi.txt` 含密钥 → **chmod 600**，且已加入 `.gitignore`，勿提交。

## 一键备份 / 恢复（.tgz，含数据库目录）

单个 `.tgz` 文件包含上述全部内容（含 data/redis、postgres）：

```bash
# 备份（默认写到桌面或 $HOME）
ai-gateway-matrix backup
ai-gateway-matrix backup ~/桌面/agm-backup.tgz

# 恢复（会 stop，并把旧目录改名为 *.pre-restore-*）
ai-gateway-matrix restore ~/桌面/agm-backup.tgz
ai-gateway-matrix start
```

## deb 安装与升级

```bash
# 首次
sudo dpkg -i ai-gateway-matrix_1.0.0_all.deb
sudo apt-get install -f   # 若缺依赖
ai-gateway-matrix start

# 升级到新版本（Key/配置自动保留）
ai-gateway-matrix backup                 # 可选但建议
sudo dpkg -i ai-gateway-matrix_新_all.deb
ai-gateway-matrix start
```

`dpkg -r` / `dpkg -P` **不会**删除 `~/.config/ai-gateway-matrix`。

## 换机

1. 旧机：`ai-gateway-matrix backup ~/agm.tgz`
2. 新机：安装 deb → `ai-gateway-matrix restore ~/agm.tgz` → `start`
3. 若启用设备绑定授权：需在新机重新签发 `.lic`

## 源码模式

仓库根即数据目录；`AI_GATEWAY_HOME` 同样可用。  
`backup` / `restore` 行为一致。
