# 可重置免费额度 · 全网扫描（2026-07）

> **可重置** = 按分钟/天/月滚动恢复，不是注册一次性送完就没。
> **一次性/试用** = 送完要充值，列表应往后排。
> 数字会变，以各家控制台为准。来源：官方文档 + [awesome-free-llm-apis](https://github.com/mnfst/awesome-free-llm-apis)（2026-06 刷新）+ 中文社区汇总。

## 一、优先接（可重置 · 本仓缺口大）

| 平台 | 类型 | 可重置额度（量级） | OpenAI 兼容 | 申请 | 本仓状态 |
|------|------|-------------------|-------------|------|----------|
| **魔搭 ModelScope** | 国内托管 | **日 2000 次**（用户总量），单模型常 ≤500/日，**每日 UTC+8 重置** | ✅ `https://api-inference.modelscope.cn/v1` | [Token](https://modelscope.cn/my/myaccesstoken)（需阿里云绑定+实名） | **已加目录+配置** |
| **Cloudflare Workers AI** | 边缘推理 | **日 10,000 Neurons**（每日重置） | ✅ `/v1/chat/completions`（需 Account ID） | [API Token](https://dash.cloudflare.com/profile/api-tokens) | 目录已标；配置需 Account ID |
| **Kilo Code Gateway** | 新聚合/抢市场 | free 模型约 **~200 req/小时** 级 | ✅ `https://api.kilo.ai/api/gateway` | [kilo.ai](https://kilo.ai) | 目录观察 |
| **LLM7.io** | 零摩擦网关 | **30 RPM**（有 token 可更高） | ✅ `https://api.llm7.io/v1` | [token.llm7.io](https://token.llm7.io) | 目录观察 |
| **Aion Labs** | 小厂永久免费 | **15 RPM · 20K tokens/日** | ✅ `https://api.aionlabs.ai/v1` | [aionlabs.ai](https://www.aionlabs.ai) | 目录观察（偏 roleplay） |
| **OVHcloud AI Endpoints** | 欧盟托管 | **匿名 2 RPM/模型/IP**（永久）；注册可更高 | ✅ Kepler endpoints | [ovhcloud.com](https://www.ovhcloud.com/en/public-cloud/ai-endpoints/) | 目录观察 |

## 二、已在本仓 · 可重置（继续用）

| 平台 | 可重置要点 | 备注 |
|------|------------|------|
| **Google Gemini** | RPM + RPD（Flash 常见日千级） | 部分地区限制；数据可能用于改进产品 |
| **Agnes AI**（观察期） | **Effective RPM** 为主：文本 flash **20**/分（Allowed 常写 30）；图 1K/2K/3K/4K=20/10/1/1；视频 1/分 | **无公开日/月总量**；以有效 RPM 计，勿只看 Allowed |
| **Groq** | RPM + RPD（约 30 RPM / ~1000 RPD 量级） | 极速；2026 曾收紧日限 |
| **Cerebras** | 官方仍有 $0 Free API 层（低限额） | Developer 按量档至少充值 $10；精确限额看控制台 |
| **OpenRouter :free** | 20 RPM · **50 RPD**（充 ≥$10 积分后 free 模型可到 **1000 RPD**） | 模型常改名；优先 `:free` |
| **Mistral Experiment** | 月 token 量级实验档 | 非商用约束以 ToS 为准 |
| **智谱 GLM Flash** | 免费档 + 并发限制 | 已配置 |
| **硅基 SiliconFlow** | **小模型（如 7B/8B）可重置 RPM/TPM**；V3/R1 等多为一次性 | 已按「可重置 / 一次性」分标 |
| **GitHub Models** | 原型 RPM/RPD | 非生产 SLA |
| **NVIDIA NIM** | 约 40 RPM 级原型 | 需开发者账号 |
| **HuggingFace Router** | 月 credits 量级 | 路由到多家 Inference Provider |
| **Vercel AI Gateway** | 月免费额度量级 | 账单页为准 |

## 三、一次性 / 试用（别当「可重置主粮」）

| 平台 | 性质 | 建议 |
|------|------|------|
| DeepSeek 官方 | 注册赠送/试用后按量 | 标 trial，用完付费 |
| SambaNova | 一次性 $5 credits，30 天到期 | 沉底；不是可重置免费层 |
| Together / Fireworks / Novita | 注册试用金 | 沉底 |
| DeepInfra | 官方当前按量付费，需正余额 | 标 paid，不计入免费节省 |
| 硅基 V3/R1 等高级 | 一次性免费额度为主 | 已沉底 |
| MiniMax 等「限时免费 API」 | 抢市场窗口，可能随时改付费 | 观察，勿当永久 |
| 各类中转站「送额度」 | 多为一次性积分 | 数据与 ToS 风险高 |

## 四、抢市场 · 新开业 / 活跃（建议每周扫一眼）

| 名字 | 为何值得盯 | 风险 |
|------|------------|------|
| **Kilo Code** | free 路由 + 多家 :free 模型，偏 coding | 模型列表会变；部分模型会记日志 |
| **LLM7** | 注册极轻、多模型、可直接 OpenAI SDK | 稳定性与数据政策自核 |
| **魔搭 API-Inference** | 国内日 2000 次、Qwen/GLM 系 | 实名；限额动态 |
| **Ollama Cloud** | 轻量免费 + session/周重置 | 非标准 OpenAI 协议 |
| **Chutes / 新 serverless 推理** | 常有新模型热部署促销 | 多为按量，少见永久 free |
| **OpenRouter 新 :free** | 每周可能上新 free 模型 | 模型名易变（本仓有 autofix） |

跟踪源（建议加书签）：

1. https://github.com/mnfst/awesome-free-llm-apis
2. https://github.com/for-the-zero/Free-LLM-Collection
3. https://openrouter.ai/models?q=free
4. 魔搭限额：https://modelscope.cn/docs/model-service/API-Inference/limits
5. Cloudflare Workers AI Pricing

## 五、接入优先级建议（对本网关）

1. **魔搭**（已接）：国内可重置主力
2. **Cloudflare Workers AI**：补国际边缘可重置（需 Account ID）
3. **OpenRouter 刷新 :free 列表** + autofix
4. **Kilo / LLM7**：观察 1～2 周稳定性后再进强路由
5. 中转站：仅 observation，默认不进敏感 trusted

---

## 自动跟进

本仓 `provider-monitor` 每小时：

1. 审计上游 `/models`（改名 autofix）
2. **`free_tier_refresh`**：拉取限额文档 → 启发式 / **顶级模型**解析 → 写入 `state/free-tier-quotas.json`
3. 仪表盘额度表自动合并该文件（置信度够才覆盖）

手动跑一轮：

```bash
python -m scripts.free_tier_refresh --once
# 或
python -m scripts.provider_discovery --once --free-tier-refresh --autofix
```

环境变量：`FREE_TIER_REFRESH_ENABLE`、`FREE_TIER_REFRESH_LLM`、`FREE_TIER_REFRESH_MIN_CONF`。

---

*生成日期：2026-07-17。额度以各站最新文档与自动跟进结果为准。*
