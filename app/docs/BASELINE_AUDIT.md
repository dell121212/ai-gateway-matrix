# Gate 0 — 基线审计（真实命令结果）

审计时间：2026-07-24  
工作目录：`/home/chenkai/文档/api`  
附件：`/home/chenkai/下载/Private-API-main (1).zip`（commit 快照 `aa4a0b4`）  
当前仓库：`df9af12`（比附件新：含 desktop/licensing/packaging）

## 环境

| 项 | 版本 |
|----|------|
| Python | 3.12.3 |
| Node | v24.16.0 / npm 11.13.0 |
| Docker | 29.1.3 |
| Docker Compose | 2.40.3 |
| venv | `.venv` 新建并安装 requirements*.txt |

## 命令结果

| 检查 | 结果 |
|------|------|
| `pytest tests/` | **100 passed, 7 failed**（约 19.9s） |
| `docker compose config` | **exit 0** |
| `python -m scripts.validate_config` | **严格配置校验通过** |
| `node --check tests/dashboard_frontend_logic.mjs` | **exit 0** |
| `ruff check gateway dashboard scripts tests` | **All checks passed** |

## 失败测试（基线，未删除）

1. `tests/test_config_editor.py::test_model_update_keeps_groq_direct_entry_in_sync_without_trusted_access`
2. `tests/test_llm_classifier_resolve.py::ClassifierResolveTests::test_auto_picks_generalcompute_when_present`
3. `tests/test_llm_classifier_resolve.py::ClassifierResolveTests::test_source_env_exclusive`
4. `tests/test_router_quality_and_tiers.py::RouterQualityAndTierTests::test_config_enforces_requested_provider_tiers`
5. `tests/test_router_quality_and_tiers.py::RouterQualityAndTierTests::test_empty_plain_text_response_is_retried_for_any_model`
6. `tests/test_router_quality_and_tiers.py::RouterQualityAndTierTests::test_siliconflow_qwen_bad_output_becomes_quality_failure`
7. `tests/test_router_quality_and_tiers.py::RouterQualityAndTierTests::test_wrong_arithmetic_is_retried_for_any_model`

## 现状事实（源码核对）

| 主题 | 事实 |
|------|------|
| 数据库 | Postgres 容器存在，主要供 LiteLLM；无项目业务表/schema |
| 用量 | `gateway/usage_tracker.py` 仅 Redis 渠道级聚合 |
| 流式 | `custom_router_hook.py` 强制 `data["stream"]=False`（约 864 行） |
| 客户端 Key | `state/client-keys.json` **明文** full key，`mode=0o600` |
| 认证 | `DASHBOARD_AUTH=local\|token`，无用户/RBAC |
| 账单 | 渠道中心 token/金额估算，无预冻结/流水/幂等 |
| 重复记账风险 | Redis 累加无请求级幂等键；回调重放可能重复计数 |
| 入口 | Dashboard 透明代理 LiteLLM，`:4000`/`:8080` |
| Dockerfile | `uvicorn dashboard.backend:app --port 8080` |

## 决策

在当前仓库原地改造（保留比附件更新的 desktop/licensing），不另起缩水 demo。  
P0 完成后进入 P1–P6 实现。
