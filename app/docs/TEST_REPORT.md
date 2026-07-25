# 测试报告（实测）

日期：2026-07-24

## Gate 0 基线

见 `docs/BASELINE_AUDIT.md`。

| 项 | 结果 |
|----|------|
| pytest 全量（基线） | 100 passed / **7 failed**（既有） |
| ruff | pass |
| validate_config | pass |
| docker compose config | pass |

既有失败（未删除）：

- `test_config_editor.py::test_model_update_keeps_groq_direct_entry_in_sync_without_trusted_access`
- `test_llm_classifier_resolve.py` ×2
- `test_router_quality_and_tiers.py` ×4

## 改造后

| 套件 | 命令 | 结果 |
|------|------|------|
| 计费/模式/安全单元测试 | `pytest tests/billing -q` | **29 passed** |
| 全量 pytest | `pytest tests/ -q` | **129 passed / 7 failed**（失败同基线） |
| 前端构建 | `cd dashboard/frontend && npm run build` | **pass** |
| 前端 vitest | `npm test` | **1 passed** |
| validate_config | `python -m scripts.validate_config` | **pass** |
| docker compose config | `docker compose config` | **pass** |
| Docker rebuild dashboard | `docker compose up -d --build dashboard` | **healthy** |

## HTTP 验收（本机 Docker）

```text
GET /healthz                     → {"status":"ok"}
GET /api/v1/system/health        → postgres=true redis=true billing_fail_mode=open
GET /api/v1/auth/status          → admin bootstrapped, authenticated
GET /console/                    → 200
GET /                            → 200
GET /api/v1/auth/me              → balance 10000 credits
POST /api/v1/api-keys            → 创建 agent-stream key（仅一次返回全文）
GET /api/v1/credit-accounts/me   → active account
```

## 未在本机完全自动化的项

| 项 | 说明 |
|----|------|
| 真实上游模型 E2E 扣费 | 故意不使用真实 API Key 自动化 |
| 50 并发真实 DB 压测 | 提供了并发余额逻辑单测；完整 DB 并发需长时压测脚本 |
| 旧 7 个失败用例 | 基线即失败，与本次账本改造无直接关系，未删测 |

## 交付物

`/home/chenkai/文档/Private-API-professional-backend.zip`
