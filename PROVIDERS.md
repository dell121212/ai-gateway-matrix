# 免费 API 渠道参考清单 (v5 全面版)

> 本文件列出 `config.yaml` 中集成的所有免费 API 渠道，按优先级和池子分组。
> 详细排名和对比请参阅 `免费LLM_API_排名与集成指南_v2.docx`。

## 四层路由池说明

| 池子 | 档位 | 用途 | 特点 | 渠道数 |
|------|------|------|------|--------|
| `fast-pool` | 弱 | 超短输入 + 简单任务 | Groq/Cerebras/SambaNova 超快推理（500-2000 tokens/sec） | 5 |
| `free-pool` | 中 | 常规任务 | GLM/Gemini/SiliconFlow/Agnes AI(观察期)/国内官方/聚合站 | 23 |
| `strong-model-pool` | 强 | 复杂任务 | Llama 405B/Gemini Pro/DeepSeek R1/Llama 70B/NVIDIA NIM（**不含 Agnes AI**——观察期渠道不承接复杂/敏感任务） | 14 |
| `trusted-pool` | — | 敏感内容专用 | 只含 `provider_manifest.yaml` 显式允许敏感数据的官方渠道 | 15（复用，非独立配置） |

## 渠道分类总览（58 个 deployment，含 trusted-pool 复用条目，不含派生 direct 分组）

### Tier 1: 永久免费层（国际）
| 渠道 | 模型 | RPM | 上下文 | 特点 |
|------|------|-----|--------|------|
| **Agnes AI**（观察期，请知情后使用） | Agnes-2.0-Flash | 20 | 1M | 自称永久免费/无Token上限；上线约一个月，"Claw-Eval"评测是自建/关联站点，非独立第三方评测 |
| Google Gemini | Gemini 3.5/2.5 Flash, 2.5 Pro | 15 | 900K+ | 官方免费层 |
| Groq | GPT-OSS 20B/120B, Qwen 3.6 27B | 30 | 131K | LPU 超快推理；Qwen 支持视觉 |
| Cerebras | GPT-OSS 120B, Z.ai GLM 4.7 | 10-30 | 120K+ | WSE 超快推理；GLM 为预览/低 RPD |
| SambaNova | Llama 3.1 405B/70B/8B | 10-50 | 120K | 唯一免费 405B |
| GLM (智谱) | GLM-4.7-Flash | 200 | 200K | 完全免费；2026-01-20 起替代 GLM-4.5-Flash |
| SiliconFlow | GLM/Qwen/DeepSeek（第三方托管） | 50-100 | 30-60K | 国内加速，非模型原厂直营 |
| Mistral AI | Mistral Small（×2 账号） | 10 | 30K | 欧洲推理；⚠️ 多账号叠加额度有 ToS 风险 |
| GitHub Models | GPT-4o-mini | 15 | 8K | 微软代理转发，免费原型层 |
| OpenRouter | Llama 3.3 70B / Mistral Nemo / Gemma 2 9B / DeepSeek R1（免费模型层） | 10-20 | 8-120K | 多供应商聚合 |
| HuggingFace | 免费推理 API | 10 | 30K | 社区模型 |
| NVIDIA NIM | Llama 3.3 70B, Nemotron 70B | 40 | 120K | NVIDIA 托管，非模型原厂 |
| DeepInfra | Llama 3.3 70B | 30 | 128K | 免费层 |

### Tier 2: 国内官方免费层
| 渠道 | 模型 | 免费额度 | 上下文 |
|------|------|----------|--------|
| 阿里云百炼 | Qwen Plus/Turbo | 100万 tokens/月 | 8-120K |
| 腾讯混元 | hunyuan-turbos | 100万 tokens | 28K |
| 百度千帆 | ERNIE Speed | 免费 | 128K |
| 月之暗面 Kimi | moonshot-v1-8k | 试用额度 | 8K |

### Tier 3: 试用额度（用完即止）
| 渠道 | 免费额度 | 备注 |
|------|----------|------|
| Together AI | $5 | 试用 |
| Novita AI | 试用额度 | 多模型 |
| Fireworks AI | 试用额度 | 快速推理 |
| Lepton AI | 试用额度 | 自定义模型 |
| DeepSeek | 试用额度 | 国产强模型 |

### Tier 4: 聚合/中转站（兜底）
| 渠道 | 免费额度 | 备注 |
|------|----------|------|
| AIHubMix | 27+ 免费模型 | OpenAI 兼容 |
| Vercel AI Gateway | $5/月 | 多供应商 |
| Glama | 免费层 | 多供应商 |
| AI/ML API | 试用 | 多模型聚合 |

## Agents 平台说明

以下 agents 平台提供 API 调用能力，但多数是工作流/bot 形式，不适合直接作为 LLM completion 端点集成到 LiteLLM。如需使用，建议通过它们的 SDK 单独调用：

| 平台 | 免费额度 | API 形式 | 集成建议 |
|------|----------|----------|----------|
| Coze (扣子) | 免费层 | Bot API | 通过 SDK 调用 bot |
| Dify Cloud | 200次/天 | App API | 通过 SDK 调用 workflow |
| FastGPT | 开源免费 | OpenAI 兼容 | 可自部署后集成 |
| PPIO | 试用额度 | OpenAI 兼容 | 可直接集成 |

## 参考项目

- [cheahjs/free-llm-api-resources](https://github.com/cheahjs/free-llm-api-resources) — 最全的免费 LLM API 列表
- [mnfst/awesome-free-llm-apis](https://github.com/mnfst/awesome-free-llm-apis) — 永久免费层精选
- [BerriAI/litellm](https://github.com/BerriAI/litellm) — 本项目使用的网关框架
- [Puter.js](https://puter.com/) — 免费无限 OpenAI API（前端 JS SDK）
