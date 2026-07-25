#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
渠道元数据目录
————————————————————————————————————————
config.yaml 只关心 LiteLLM 需要的字段（model/api_key/rpm/priority...），
展示用元数据单独放这里：供应商名、申请地址、信任等级、**收费/免费与如何免费**。

billing 取值：
  · free           官方/文档标明有可用免费层（额度内 $0）
  · trial          注册送试用额度，用完后需充值或失效
  · free_plus_paid 同时存在免费模型/额度 与 付费按量
  · paid           以按量/套餐付费为主（无稳定免费层）

how_free_zh：
  仅当 billing 不是 paid 时必填——写清「去哪注册、点哪里拿 Key、免费条件/限额概要」。
  额度会变，文案写「以控制台为准」，并给官方申请链接。
"""

from __future__ import annotations

import re as _re

from typing import NotRequired, TypedDict


class ProviderInfo(TypedDict):
    name: str
    signup_url: str
    trust: str
    note: str
    # free | trial | free_plus_paid | paid
    billing: NotRequired[str]
    # 卡片角标短文案
    pricing_label_zh: NotRequired[str]
    # 如何免费 / 免费条件（免费·试用·混合必填）
    how_free_zh: NotRequired[str]
    # 计费补充（付费如何扣费等）
    pricing_detail_zh: NotRequired[str]


# key = config.yaml 里 os.environ/XXX 的变量名
PROVIDER_CATALOG: dict[str, ProviderInfo] = {
    "GLM_API_KEY": {
        "name": "智谱 GLM",
        "signup_url": "https://open.bigmodel.cn/usercenter/proj-mgmt/apikeys",
        "trust": "official",
        "note": "GLM-4.7-Flash 等开放平台模型",
        "billing": "free",
        "pricing_label_zh": "免费层",
        "how_free_zh": (
            "打开 open.bigmodel.cn 注册 → 控制台「API Keys」创建密钥 → "
            "选用文档标明免费/开放的模型（如 glm-4.7-flash）。"
            "免费层有 RPM/套餐限额，以控制台与价目页为准。"
        ),
        "pricing_detail_zh": "部分旗舰模型按量付费；本网关默认走免费档模型。",
    },
    "GEMINI_API_KEY": {
        "name": "Google Gemini",
        "signup_url": "https://aistudio.google.com/apikey",
        "trust": "official",
        "note": "Google AI Studio 官方 API",
        "billing": "free",
        "pricing_label_zh": "免费层",
        "how_free_zh": (
            "打开 aistudio.google.com → 登录 Google 账号 → 「Get API key」创建密钥 → "
            "免费层可调 Flash/Pro 等（RPM/RPD 按模型与 usage tier）。"
            "详见 ai.google.dev/gemini-api/docs/rate-limits；超额或付费项目另计。"
        ),
        "pricing_detail_zh": "升级付费项目后按 Google Cloud / AI Studio 价目计费。",
    },
    "GROQ_API_KEY": {
        "name": "Groq",
        "signup_url": "https://console.groq.com/keys",
        "trust": "official",
        "note": "LPU 超快推理",
        "billing": "free",
        "pricing_label_zh": "免费层",
        "how_free_zh": (
            "打开 console.groq.com 注册 → API Keys 创建 → "
            "免费层按模型有 RPM/RPD/TPM（见 console.groq.com/docs/rate-limits）。"
            "无需绑卡即可用；Developer 计划变更以官网为准。"
        ),
        "pricing_detail_zh": "官方另有付费/更高限额档。",
    },
    "CEREBRAS_API_KEY": {
        "name": "Cerebras",
        "signup_url": "https://cloud.cerebras.ai/",
        "trust": "official",
        "note": "WSE 超快推理；官方仍提供低限额 Free API 层",
        "billing": "free_plus_paid",
        "pricing_label_zh": "免费层+按量",
        "how_free_zh": (
            "打开 cloud.cerebras.ai 注册并创建 API Key。官方 Pricing 当前仍列出 $0 Free，"
            "可访问 Cerebras 托管模型但限额较低；Developer 档需至少充值 $10。"
            "精确限额与可用模型以控制台为准。"
        ),
        "pricing_detail_zh": (
            "Developer 为按 token 付费并提供约 10 倍于 Free 的限额；Free 适合实验，不保证生产容量。"
        ),
    },
    "SAMBANOVA_API_KEY": {
        "name": "SambaNova",
        "signup_url": "https://cloud.sambanova.ai/apis",
        "trust": "official",
        "note": "可及 Llama 405B 等大模型",
        "billing": "trial",
        "pricing_label_zh": "$5 / 30天试用",
        "how_free_zh": (
            "打开 cloud.sambanova.ai 注册 → APIs/Keys 创建密钥 → "
            "新开发者获得一次性 $5 API credits，官方说明 30 天到期；用完后需绑卡按量。"
            "Key 须为纯 ASCII 令牌，勿粘贴中文备注。"
        ),
        "pricing_detail_zh": "试用金不是每日/月度重置免费层；到期或耗尽后按模型价目付费。",
    },
    "MISTRAL_KEY_1": {
        "name": "Mistral",
        "signup_url": "https://console.mistral.ai/api-keys/",
        "trust": "official",
        "note": "弱 Ministral8B / 中 Small / 强 Medium / 顶级 Large（实验档限流）",
        "billing": "free",
        "pricing_label_zh": "实验档可重置限流",
        "how_free_zh": (
            "打开 console.mistral.ai 注册 → API Keys 创建。"
            "Experiment/free 在限流内可调 Ministral、Small、Medium、Large 等；"
            "额度按池共享、会变，以控制台为准。生产请改付费档。"
        ),
    },
    "MISTRAL_KEY_2": {
        "name": "Mistral",
        "signup_url": "https://console.mistral.ai/api-keys/",
        "trust": "official",
        "note": "⚠️ 多账号叠加额度可能违反 ToS",
        "billing": "free",
        "pricing_label_zh": "免费/实验档",
        "how_free_zh": (
            "同 Mistral 主账号的注册与领 Key 方式。"
            "用第二账号叠加免费额度大概率违反服务条款，仅个人实验，勿用于业务。"
        ),
    },
    "DEEPSEEK_API_KEY": {
        "name": "DeepSeek 官方",
        "signup_url": "https://platform.deepseek.com/api_keys",
        "trust": "official",
        "note": "按量扣余额",
        "billing": "trial",
        "pricing_label_zh": "试用后按量",
        "how_free_zh": (
            "打开 platform.deepseek.com 注册 → API Keys 创建 → "
            "新账号常有赠送/试用余额，用完后按官方价目扣费。"
            "余额可 GET /user/balance 查询。"
        ),
        "pricing_detail_zh": "按输入/输出 token 从账户余额扣费。",
    },
    "DASHSCOPE_API_KEY": {
        "name": "阿里云百炼",
        "signup_url": "https://bailian.console.aliyun.com/?tab=model#/api-key",
        "trust": "official",
        "note": "通义千问官方",
        "billing": "trial",
        "pricing_label_zh": "新人一次性额度",
        "how_free_zh": (
            "打开 bailian.console.aliyun.com（需阿里云账号）→ 创建 API-KEY → "
            "首次开通后各模型通常获独立新人额度；官方当前说明有效期为 30～90 天，"
            "到期不补发、不延期、不重置，之后按量计费。"
        ),
        "pricing_detail_zh": "超出免费额度后按百炼价目扣费。",
    },
    "HUNYUAN_API_KEY": {
        "name": "腾讯混元",
        "signup_url": "https://console.cloud.tencent.com/hunyuan/start",
        "trust": "official",
        "note": "腾讯云混元",
        "billing": "free_plus_paid",
        "pricing_label_zh": "有免费额度",
        "how_free_zh": (
            "打开腾讯云混元控制台 → 开通服务并创建密钥 → "
            "新用户/活动常见赠送免费 tokens（数量以控制台为准）→ 超额按量。"
        ),
        "pricing_detail_zh": "免费额度外按腾讯云计费。",
    },
    "QIANFAN_API_KEY": {
        "name": "百度千帆",
        "signup_url": "https://console.bce.baidu.com/qianfan/ais/console/applicationConsole/application",
        "trust": "official",
        "note": "ERNIE 等",
        "billing": "free_plus_paid",
        "pricing_label_zh": "有免费层",
        "how_free_zh": (
            "打开百度智能云千帆控制台 → 创建应用/获取 API Key → "
            "部分模型（如 ERNIE Speed 类）提供免费调用额度，以产品页与控制台为准。"
        ),
        "pricing_detail_zh": "商用/旗舰模型按量或资源包计费。",
    },
    "MOONSHOT_API_KEY": {
        "name": "月之暗面 Kimi",
        "signup_url": "https://platform.moonshot.cn/console/api-keys",
        "trust": "official",
        "note": "Kimi 开放平台",
        "billing": "trial",
        "pricing_label_zh": "试用后按量",
        "how_free_zh": (
            "打开 platform.moonshot.cn 注册 → 控制台 API Keys → "
            "新用户常有试用余额/代金券，用完后按量付费。"
            "可查 GET /v1/users/me/balance。"
        ),
        "pricing_detail_zh": "按 token 从余额扣费。",
    },
    "SILICONFLOW_API_KEY": {
        "name": "硅基流动 SiliconFlow",
        "signup_url": "https://cloud.siliconflow.cn/account/ak",
        "trust": "third_party",
        "note": "7B/8B 可重置 RPM；高级模型多为一次性免费额度",
        "billing": "free_plus_paid",
        "pricing_label_zh": "小模型可重置·高级一次性",
        "how_free_zh": (
            "打开 cloud.siliconflow.cn 注册 → 账户 API 密钥。"
            "可重置免费主要是小模型（如 Qwen2.5-7B / Qwen3-8B，RPM/TPM 日更）；"
            "DeepSeek-V3/R1 等高级模型多为注册一次性免费额度，用完后按量扣余额。"
            "仪表盘把「一次性免费」排在后面，优先用可重置小模型。"
        ),
        "pricing_detail_zh": "一次性额度用尽后按硅基价目扣人民币余额。",
    },
    "MODELSCOPE_API_KEY": {
        "name": "魔搭 ModelScope",
        "signup_url": "https://modelscope.cn/my/myaccesstoken",
        "trust": "third_party",
        "note": "阿里系开源模型托管；日调用可重置",
        "billing": "free",
        "pricing_label_zh": "日2000次可重置",
        "how_free_zh": (
            "注册 modelscope.cn → 绑定阿里云并实名 → 我的 → Access Token。"
            "API-Inference：每用户约 2000 次/日（总量），单模型常 ≤500 次/日，"
            "每日 UTC+8 00:00 重置。Base: https://api-inference.modelscope.cn/v1"
        ),
        "pricing_detail_zh": "免费体验向；高并发请走百炼等付费。",
    },
    "CLOUDFLARE_API_TOKEN": {
        "name": "Cloudflare Workers AI",
        "signup_url": "https://dash.cloudflare.com/profile/api-tokens",
        "trust": "third_party",
        "note": "边缘推理；需 Account ID",
        "billing": "free",
        "pricing_label_zh": "日10k Neurons可重置",
        "how_free_zh": (
            "Cloudflare 账号 → API Tokens → Workers AI 权限。"
            "免费约 10,000 Neurons/日（每日重置）。"
            "OpenAI 兼容需 Account ID："
            "https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1"
        ),
        "pricing_detail_zh": "超额需 Workers Paid；Neurons 按模型折算。",
    },
    "KILO_API_KEY": {
        "name": "Kilo Code",
        "signup_url": "https://kilo.ai",
        "trust": "observation",
        "note": "新聚合网关；free 模型抢市场",
        "billing": "free",
        "pricing_label_zh": "free模型可重置",
        "how_free_zh": (
            "kilo.ai 注册取 Key。Base 约 https://api.kilo.ai/api/gateway ；"
            "free 路由/多模型 :free，限额约百级 req/小时（以控制台为准）。"
            "新开业聚合，模型列表与 ToS 易变，观察期接入。"
        ),
        "pricing_detail_zh": "部分 free 模型可能记录 prompt；勿传敏感。",
    },
    "LLM7_API_KEY": {
        "name": "LLM7.io",
        "signup_url": "https://token.llm7.io",
        "trust": "observation",
        "note": "零摩擦多模型网关",
        "billing": "free",
        "pricing_label_zh": "约30RPM可重置",
        "how_free_zh": (
            "token.llm7.io 取 token；Base https://api.llm7.io/v1 ；"
            "常见约 30 RPM（有 token 可更高）。偏抢市场网关，自核稳定性。"
        ),
        "pricing_detail_zh": "以站点最新限额为准。",
    },
    "COHERE_API_KEY": {
        "name": "Cohere",
        "signup_url": "https://dashboard.cohere.com/api-keys",
        "trust": "official",
        "note": "Trial Key；月调用可重置量级",
        "billing": "trial",
        "pricing_label_zh": "月约1000次Trial",
        "how_free_zh": (
            "dashboard.cohere.com 创建 Trial API Key（通常无需绑卡）。"
            "约 1000 API calls/月量级 + RPM 限制；偏非商用约束看 ToS。"
        ),
        "pricing_detail_zh": "Trial 用尽后按 Cohere 价目。",
    },
    "AION_API_KEY": {
        "name": "Aion Labs",
        "signup_url": "https://www.aionlabs.ai",
        "trust": "observation",
        "note": "永久免费小厂；偏角色扮演",
        "billing": "free",
        "pricing_label_zh": "15RPM·20K TPD",
        "how_free_zh": (
            "aionlabs.ai 注册取 Key；Base https://api.aionlabs.ai/v1 ；"
            "约 15 RPM / 20K tokens/日，永久 free tier。"
        ),
        "pricing_detail_zh": "垂直 roleplay，通用任务一般。",
    },
    "OPENROUTER_API_KEY": {
        "name": "OpenRouter",
        "signup_url": "https://openrouter.ai/settings/keys",
        "trust": "third_party",
        "note": "多供应商聚合",
        "billing": "free_plus_paid",
        "pricing_label_zh": "免费模型+积分",
        "how_free_zh": (
            "打开 openrouter.ai 注册 → Settings → Keys 创建 → "
            "选用模型 ID 带 :free 的免费路由（有 RPD 等限制）→ "
            "付费模型需账户积分/充值。限额见 openrouter.ai/docs。"
        ),
        "pricing_detail_zh": "非 :free 模型按路由价格扣 credits。",
    },
    "GITHUB_TOKEN": {
        "name": "GitHub Models",
        "signup_url": "https://github.com/settings/tokens",
        "trust": "third_party",
        "note": "微软代理的原型层",
        "billing": "free",
        "pricing_label_zh": "免费原型",
        "how_free_zh": (
            "GitHub 账号登录 → 开通 Models 访问 → 创建 PAT；fine-grained Token 必须授予 "
            "Models: read（classic PAT 使用 models scope）→ 调用 models.github.ai/inference。"
            "仅限原型/低限额，非生产 SLA。"
        ),
        "pricing_detail_zh": "超出原型额度后需改用 Azure/其它付费通道。",
    },
    "HF_TOKEN": {
        "name": "HuggingFace Inference",
        "signup_url": "https://huggingface.co/settings/tokens",
        "trust": "third_party",
        "note": "社区推理 API",
        "billing": "free_plus_paid",
        "pricing_label_zh": "免费额度+Pro",
        "how_free_zh": (
            "打开 huggingface.co 注册 → Settings → Access Tokens 创建 → "
            "免费账号可调部分 Inference API/Endpoints（有速率与模型限制）；"
            "Pro/付费 Inference 另计。"
        ),
        "pricing_detail_zh": "付费 Endpoint / Pro 按 HuggingFace 账单。",
    },
    "NVIDIA_API_KEY": {
        "name": "NVIDIA NIM",
        "signup_url": "https://build.nvidia.com/settings/api-keys",
        "trust": "third_party",
        "note": "build.nvidia.com",
        "billing": "free",
        "pricing_label_zh": "免费层",
        "how_free_zh": (
            "打开 build.nvidia.com 注册 NGC/NVIDIA 账号 → API Keys 创建 → "
            "对列出的 NIM 模型有免费试用/速率限制调用。生产高并发需企业方案。"
        ),
    },
    "DEEPINFRA_API_KEY": {
        "name": "DeepInfra",
        "signup_url": "https://deepinfra.com/dash/api_keys",
        "trust": "third_party",
        "note": "第三方推理",
        "billing": "paid",
        "pricing_label_zh": "按量付费",
        "how_free_zh": "",
        "pricing_detail_zh": (
            "官方当前以低价按量付费为主，未承诺稳定免费层；账户需有正余额才可调用。"
            "按模型输入/输出价目从余额扣费。"
        ),
    },
    "NOVITA_API_KEY": {
        "name": "Novita AI",
        "signup_url": "https://novita.ai/settings",
        "trust": "third_party",
        "note": "多模型推理",
        "billing": "trial",
        "pricing_label_zh": "试用额度",
        "how_free_zh": (
            "打开 novita.ai 注册 → Settings/控制台领取 API Key → "
            "新用户试用额度用完后需充值。"
        ),
        "pricing_detail_zh": "试用后按量付费。",
    },
    "FIREWORKS_API_KEY": {
        "name": "Fireworks AI",
        "signup_url": "https://fireworks.ai/account/api-keys",
        "trust": "third_party",
        "note": "快速推理托管",
        "billing": "trial",
        "pricing_label_zh": "试用额度",
        "how_free_zh": (
            "打开 fireworks.ai 注册 → Account → API Keys → "
            "新账号试用 credits，耗尽后按量。"
        ),
        "pricing_detail_zh": "按 Fireworks 价目扣费。",
    },
    "LEPTON_API_KEY": {
        "name": "Lepton AI",
        "signup_url": "https://dashboard.lepton.ai/",
        "trust": "third_party",
        "note": "推理/部署平台",
        "billing": "trial",
        "pricing_label_zh": "试用额度",
        "how_free_zh": (
            "打开 dashboard.lepton.ai 注册 → 创建 token → "
            "以平台赠送/试用额度为准，用完需付费。"
        ),
    },
    "TOGETHER_API_KEY": {
        "name": "Together AI",
        "signup_url": "https://api.together.xyz/settings/api-keys",
        "trust": "third_party",
        "note": "开源模型托管",
        "billing": "trial",
        "pricing_label_zh": "试用额度",
        "how_free_zh": (
            "打开 api.together.xyz 注册 → Settings → API Keys → "
            "新账号常见赠送试用金（如数美元量级，以注册页为准），用完按量。"
        ),
        "pricing_detail_zh": "按 Together 模型价目扣费。",
    },
    "GENERALCOMPUTE_API_KEY": {
        "name": "General Compute",
        "signup_url": "https://app.generalcompute.com/dashboard",
        "trust": "third_party",
        "note": "按量付费 API",
        "billing": "paid",
        "pricing_label_zh": "按量付费",
        "how_free_zh": "",
        "pricing_detail_zh": (
            "需在 app.generalcompute.com 充值后调用；"
            "按输入/输出 token 计费。模型 ID 填控制台实际可用名（如 minimax-m2.7）。"
            "无长期免费层。"
        ),
    },
    "AGNES_API_KEY": {
        "name": "Agnes AI",
        "signup_url": "https://platform.agnes-ai.com/settings/apiKeys",
        "trust": "observation",
        "note": "观察期；免费以 Effective RPM 限流",
        "billing": "free",
        "pricing_label_zh": "免费·按有效RPM",
        "how_free_zh": (
            "打开 platform.agnes-ai.com 注册 → Settings → API Keys。"
            "免费账户以 Effective RPM 为准（勿只看 Allowed）："
            "文本 agnes-2.0-flash 实际约 20 次/分钟（Allowed 常写 30）；"
            "图片 1K/2K/3K/4K 分别为 20/10/1/1 张/分钟；视频 1 次/分钟。"
            "公开文档未给日/月调用总量，现行主要靠每分钟频率限制。"
            "上线时间短、评测与条款需自行核实；本网关不用于敏感内容。"
        ),
        "pricing_detail_zh": (
            "无公开日/周/月 Token 包；持续可用但受 RPM 约束。"
            "条款与额度可能突变，仅作观察期备用。"
        ),
    },
    "AIHUBMIX_API_KEY": {
        "name": "AIHubMix",
        "signup_url": "https://aihubmix.com/token",
        "trust": "relay",
        "note": "聚合/中转",
        "billing": "free_plus_paid",
        "pricing_label_zh": "免费模型+充值",
        "how_free_zh": (
            "打开 aihubmix.com 注册 → Token 管理创建密钥 → "
            "平台提供部分免费模型列表；其它模型需账户余额。"
            "中转站稳定性与数据安全需自担。"
        ),
        "pricing_detail_zh": "非免费路由从账户余额扣费。",
    },
    "VERCEL_AI_API_KEY": {
        "name": "Vercel AI Gateway",
        "signup_url": "https://vercel.com/account/tokens",
        "trust": "relay",
        "note": "Vercel 网关",
        "billing": "free_plus_paid",
        "pricing_label_zh": "月免费额度",
        "how_free_zh": (
            "Vercel 账号 → Account Tokens 创建 → 启用 AI Gateway → "
            "常见每月免费额度（如数美元量级，以 Vercel 账单页为准），超额走付费。"
        ),
        "pricing_detail_zh": "超额按 Vercel AI Gateway 计费。",
    },
    "GLAMA_API_KEY": {
        "name": "Glama",
        "signup_url": "https://glama.ai/settings/gateway",
        "trust": "relay",
        "note": "多供应商网关",
        "billing": "free_plus_paid",
        "pricing_label_zh": "免费层+付费",
        "how_free_zh": (
            "打开 glama.ai 注册 → Gateway 设置获取 API Key → "
            "免费层模型/额度以 Glama 文档与控制台为准，超出后付费。"
        ),
    },
    "AIMLAPI_API_KEY": {
        "name": "AI/ML API",
        "signup_url": "https://aimlapi.com/app/keys",
        "trust": "relay",
        "note": "多模型聚合",
        "billing": "trial",
        "pricing_label_zh": "试用",
        "how_free_zh": (
            "打开 aimlapi.com 注册 → App → Keys → "
            "新用户试用额度，用完需充值。"
        ),
        "pricing_detail_zh": "试用后按平台价目。",
    },
}


_BILLING_DEFAULTS = {
    "free": "免费层",
    "trial": "试用额度",
    "free_plus_paid": "免费+付费",
    "paid": "按量付费",
    "free_or_trial": "免费/试用",
}

_ACCOUNT_ENV_RE = _re.compile(r"^(.*)_(\d+)$")

# free_quota_kind:
#   resettable — 额度可随时间重置的免费（优先用）
#   once       — 注册/活动一次性免费，用完即止（列表往后排）
#   paid       — 基本按量
#   None       — 未单独标注，跟公司 billing 走
#
# 硅基：用户确认仅 7B 小模型可重置；V3/R1/9B 等高级为一次性免费额度。
_SILICONFLOW_RESETTABLE = (
    "qwen2.5-7b",
    "qwen/qwen2.5-7b",
    "qwen3-8b",
    "qwen/qwen3-8b",
    "deepseek-r1-distill-qwen-7b",
)
_SILICONFLOW_ONCE = (
    "deepseek-v3",
    "deepseek-r1",
    "deepseek-ai/deepseek",
    "glm-4-9b",
    "zhipuai/glm-4-9b",
)


def free_quota_kind(env_var: str, model: str) -> str | None:
    """模型级免费性质：resettable | once | paid | None。"""
    env = (env_var or "").upper()
    m = (model or "").lower()
    if not env or not m:
        return None
    # 去掉 litellm 前缀噪音
    if env in {"SILICONFLOW_API_KEY"} or env.startswith("SILICONFLOW_API_KEY"):
        for token in _SILICONFLOW_RESETTABLE:
            if token in m:
                return "resettable"
        for token in _SILICONFLOW_ONCE:
            if token in m:
                return "once"
        # 硅基其它模型默认当一次性/按量，勿抢在 7B 前面
        return "once"
    # 明确可重置日/月额度的平台
    if env == "CEREBRAS_API_KEY" or env.startswith("CEREBRAS_API_KEY"):
        return "resettable"
    if env == "SAMBANOVA_API_KEY" or env.startswith("SAMBANOVA_API_KEY"):
        return "once"
    if env in {
        "MODELSCOPE_API_KEY",
        "CLOUDFLARE_API_TOKEN",
        "GROQ_API_KEY",
        "GEMINI_API_KEY",
        "GLM_API_KEY",
        "KILO_API_KEY",
        "LLM7_API_KEY",
        "AION_API_KEY",
        "COHERE_API_KEY",
        "GITHUB_TOKEN",
        "HF_TOKEN",
    } or env.startswith("MISTRAL_KEY"):
        return "resettable"
    if env == "OPENROUTER_API_KEY" and (":free" in m or m.endswith("/free")):
        return "resettable"
    return None


def free_quota_label_zh(kind: str | None) -> str:
    return {
        "resettable": "可重置免费",
        "once": "一次性免费",
        "paid": "按量",
    }.get(kind or "", "")


def company_id_from_env(env_var: str) -> str:
    """同一公司多账号共用 id：MISTRAL_KEY_1/2 → MISTRAL_KEY，GROQ_API_KEY_2 → GROQ_API_KEY。"""
    if not env_var:
        return "unknown"
    m = _ACCOUNT_ENV_RE.match(env_var)
    return m.group(1) if m else env_var


def account_index_from_env(env_var: str) -> int:
    if not env_var:
        return 1
    m = _ACCOUNT_ENV_RE.match(env_var)
    return int(m.group(2)) if m else 1


def catalog_lookup_key(env_var: str) -> str:
    """为 FOO_API_KEY_2 找到目录里的 FOO_API_KEY / FOO_API_KEY_1。"""
    if not env_var:
        return ""
    if env_var in PROVIDER_CATALOG:
        return env_var
    m = _ACCOUNT_ENV_RE.match(env_var)
    if not m:
        return env_var
    base = m.group(1)
    if base in PROVIDER_CATALOG:
        return base
    if f"{base}_1" in PROVIDER_CATALOG:
        return f"{base}_1"
    return base


def get_provider_info(env_var: str) -> ProviderInfo:
    """拿不到目录信息时给通用兜底，保证前端不因漏收录而报错。"""
    lookup = catalog_lookup_key(env_var)
    if lookup in PROVIDER_CATALOG:
        info = dict(PROVIDER_CATALOG[lookup])  # type: ignore[arg-type]
    elif env_var in PROVIDER_CATALOG:
        info = dict(PROVIDER_CATALOG[env_var])  # type: ignore[arg-type]
    else:
        info = {
            "name": (lookup or env_var).replace("_API_KEY", "").replace("_TOKEN", "").replace("_KEY", "").replace("_", " ").title(),
            "signup_url": "",
            "trust": "third_party",
            "note": "",
            "billing": "free_or_trial",
            "pricing_label_zh": "未标注",
            "how_free_zh": "请到该厂商官网注册并创建 API Key；是否免费以官方文档为准。",
        }
    # 公司名去掉历史「（账号 N）」后缀，账号序号由前端单独展示
    name = str(info.get("name") or "")
    name = _re.sub(r"（账号\s*\d+）|\(account\s*\d+\)", "", name, flags=_re.I).strip()
    info["name"] = name
    billing = info.get("billing") or "free_or_trial"
    info["billing"] = billing
    if not info.get("pricing_label_zh"):
        info["pricing_label_zh"] = _BILLING_DEFAULTS.get(billing, billing)
    if billing != "paid" and not info.get("how_free_zh"):
        url = info.get("signup_url") or "官网"
        info["how_free_zh"] = f"到 {url} 注册并创建 API Key；免费条件以厂商控制台为准。"
    if billing == "paid" and not info.get("pricing_detail_zh"):
        info["pricing_detail_zh"] = "按厂商价目付费；无长期免费层（除非另有活动）。"
    return info  # type: ignore[return-value]
