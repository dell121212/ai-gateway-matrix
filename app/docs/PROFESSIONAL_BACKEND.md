# 专业后台与实时积分系统

## 架构

- **永久真账**：PostgreSQL schema `private_api`（与 LiteLLM 表隔离）
- **实时事件**：Redis Streams（`privateapi:task:{id}:events` / `privateapi:user:{id}:events`）
- **渠道聚合用量**：仍由 `gateway/usage_tracker.py` Redis 提供（运营视图，非正式账本）
- **管理 API**：`/api/v1/*`
- **React 控制台**：`/console`（构建产物 `dashboard/frontend/dist`）
- **经典渠道台**：`/`（保留 `dashboard/static/index.html`）
- **OpenAI 兼容**：`/v1/*` 经 Dashboard 透明代理 → LiteLLM

## 积分单位

| 单位 | 含义 |
|------|------|
| microcredits | 1e-6 积分（BIGINT） |
| microusd | 1e-6 美元（BIGINT） |

三种价值分离：

1. `actual_cost` — 供应商真实支出（免费渠道可为 0）
2. `market_value` — 公开价/估价表
3. `credits` — 用户扣费

默认：`credits = market_value_usd × CREDITS_PER_USD × multiplier`

## 请求模式

| 模式 | 用途 | 流式 | 质检失败 |
|------|------|------|----------|
| `strict` | 普通问答/报告 | 强制非流式 | 换 peer 重试 |
| `agent-stream` | Cline / Roo Code | 保留 stream | 仅标记/冷却，不拼接 |

解析顺序：`X-PrivateAPI-Mode` → body metadata → API Key `default_mode` → `DEFAULT_REQUEST_MODE`

## 认证

| DASHBOARD_AUTH | 行为 |
|----------------|------|
| `local` | 本机免登录（默认） |
| `token` | `X-Dashboard-Token` / Bearer |
| `accounts` | 用户名密码 + HttpOnly Cookie + RBAC |

## Cline / Roo Code

```
Base URL: http://127.0.0.1:4000/v1
API Key:  控制台创建的客户端 Key
Model:    auto-route
Header:   X-PrivateAPI-Mode: agent-stream
Optional: X-PrivateAPI-Task-ID / X-PrivateAPI-Client: cline
```

余额不足返回 HTTP **402**，不会先打上游。

## 迁移旧 Key

启动时 `bootstrap` 会：

1. 读取 `state/client-keys.json`
2. 备份为 `*.pre-migration-*.bak`（0600）
3. 导入哈希与元数据到 `api_keys`
4. 洗掉明文 `key` 字段

## 对账

```bash
python -m scripts.reconcile_ledger --check
python -m scripts.reconcile_ledger --repair-safe   # 仅安全修复（如负冻结）
```

## 验收

```bash
bash scripts/acceptance/run_all.sh
```
