#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
渠道元数据目录 (v1)
————————————————————————————————————————
config.yaml 只关心 LiteLLM 需要的字段（model/api_key/rpm/priority...），
不应该再塞一堆"展示用"的元数据进去搅浑它。这些纯展示信息（人类可读的
供应商名字、官网/申请地址、信任等级）单独放在这里，由
dashboard/backend.py 在解析 config.yaml 得到的渠道列表基础上做关联展示。

信任等级（trust）说明：
  · official     官方直营：模型的实际调用方就是模型/硬件的厂商自己
                 （比如 Groq 自己的 LPU、Google 自己的 Gemini）
  · third_party   第三方推理托管/聚合：渠道本身是可靠公司，但你的
                 请求会先经过它，再转发给真正的模型厂商
                 （比如 SiliconFlow、OpenRouter、DeepInfra）
  · observation   观察期：上线时间很短、缺乏第三方审计或口碑积累的新渠道
                 （目前只有 Agnes AI 属于这一类）
  · relay        中转/聚合站：把多个供应商的模型包装成统一接口对外提供，
                 通常是稳定性和数据安全性都最难保证的一类
                 （AIHubMix / Vercel AI Gateway / Glama / AI/ML API）

这里的 trust 只是"展示提示"，真正影响路由安全的判断（敏感内容永远不
去 Agnes AI/中转站）写在 gateway/custom_router_hook.py 和 config.yaml 里，
不依赖这份目录——就算这份目录写错了，也不会让敏感内容泄漏出去。
"""

from __future__ import annotations

from typing import TypedDict


class ProviderInfo(TypedDict):
    name: str
    signup_url: str
    trust: str
    note: str


# key 是 config.yaml 里 "os.environ/XXX" 引用的变量名（不带 os.environ/ 前缀）
PROVIDER_CATALOG: dict[str, ProviderInfo] = {
    "GLM_API_KEY": {
        "name": "智谱 GLM",
        "signup_url": "https://open.bigmodel.cn/",
        "trust": "official",
        "note": "GLM-4.7-Flash 官方免费层",
    },
    "GEMINI_API_KEY": {
        "name": "Google Gemini",
        "signup_url": "https://aistudio.google.com/apikey",
        "trust": "official",
        "note": "Gemini 3.5/2.5 Flash + 2.5 Pro 官方免费层",
    },
    "GROQ_API_KEY": {
        "name": "Groq",
        "signup_url": "https://console.groq.com/keys",
        "trust": "official",
        "note": "LPU 超快推理，官方免费层",
    },
    "CEREBRAS_API_KEY": {
        "name": "Cerebras",
        "signup_url": "https://cloud.cerebras.ai/",
        "trust": "official",
        "note": "WSE 超快推理，官方免费层",
    },
    "SAMBANOVA_API_KEY": {
        "name": "SambaNova",
        "signup_url": "https://cloud.sambanova.ai/",
        "trust": "official",
        "note": "唯一能白嫖到 405B 的渠道",
    },
    "MISTRAL_KEY_1": {
        "name": "Mistral（账号 1）",
        "signup_url": "https://console.mistral.ai/",
        "trust": "official",
        "note": "官方免费层",
    },
    "MISTRAL_KEY_2": {
        "name": "Mistral（账号 2）",
        "signup_url": "https://console.mistral.ai/",
        "trust": "official",
        "note": "⚠️ 多账号叠加额度，注意 ToS 风险",
    },
    "DEEPSEEK_API_KEY": {
        "name": "DeepSeek 官方",
        "signup_url": "https://platform.deepseek.com/",
        "trust": "official",
        "note": "试用额度，极便宜",
    },
    "DASHSCOPE_API_KEY": {
        "name": "阿里云百炼",
        "signup_url": "https://bailian.console.aliyun.com/",
        "trust": "official",
        "note": "通义千问官方免费层",
    },
    "HUNYUAN_API_KEY": {
        "name": "腾讯混元",
        "signup_url": "https://cloud.tencent.com/product/hunyuan",
        "trust": "official",
        "note": "官方免费层",
    },
    "QIANFAN_API_KEY": {
        "name": "百度千帆",
        "signup_url": "https://qianfan.baidubce.com/",
        "trust": "official",
        "note": "ERNIE Speed 官方免费层",
    },
    "MOONSHOT_API_KEY": {
        "name": "月之暗面 Kimi",
        "signup_url": "https://platform.moonshot.cn/",
        "trust": "official",
        "note": "官方免费试用额度",
    },
    "SILICONFLOW_API_KEY": {
        "name": "硅基流动 SiliconFlow",
        "signup_url": "https://cloud.siliconflow.cn/",
        "trust": "third_party",
        "note": "第三方托管 Qwen/DeepSeek/GLM",
    },
    "OPENROUTER_API_KEY": {
        "name": "OpenRouter",
        "signup_url": "https://openrouter.ai/keys",
        "trust": "third_party",
        "note": "聚合平台，免费模型层",
    },
    "GITHUB_TOKEN": {
        "name": "GitHub Models",
        "signup_url": "https://github.com/marketplace/models",
        "trust": "third_party",
        "note": "微软代理转发，免费原型额度",
    },
    "HF_TOKEN": {
        "name": "HuggingFace Inference",
        "signup_url": "https://huggingface.co/settings/tokens",
        "trust": "third_party",
        "note": "社区托管模型",
    },
    "NVIDIA_API_KEY": {
        "name": "NVIDIA NIM",
        "signup_url": "https://build.nvidia.com/",
        "trust": "third_party",
        "note": "NVIDIA 官方托管，非模型原厂",
    },
    "DEEPINFRA_API_KEY": {
        "name": "DeepInfra",
        "signup_url": "https://deepinfra.com/",
        "trust": "third_party",
        "note": "第三方推理托管",
    },
    "NOVITA_API_KEY": {
        "name": "Novita AI",
        "signup_url": "https://novita.ai/",
        "trust": "third_party",
        "note": "第三方推理托管，试用额度",
    },
    "FIREWORKS_API_KEY": {
        "name": "Fireworks AI",
        "signup_url": "https://fireworks.ai/",
        "trust": "third_party",
        "note": "第三方推理托管，试用额度",
    },
    "LEPTON_API_KEY": {
        "name": "Lepton AI",
        "signup_url": "https://www.lepton.ai/",
        "trust": "third_party",
        "note": "第三方推理托管，试用额度",
    },
    "TOGETHER_API_KEY": {
        "name": "Together AI",
        "signup_url": "https://api.together.xyz/",
        "trust": "third_party",
        "note": "第三方推理托管，试用额度",
    },
    "AGNES_API_KEY": {
        "name": "Agnes AI",
        "signup_url": "https://platform.agnes-ai.com/settings/apiKeys",
        "trust": "observation",
        "note": "上线约一个月，20 RPM 免费，条款/稳定性未经长期验证",
    },
    "AIHUBMIX_API_KEY": {
        "name": "AIHubMix",
        "signup_url": "https://aihubmix.com/",
        "trust": "relay",
        "note": "多模型聚合中转站",
    },
    "VERCEL_AI_API_KEY": {
        "name": "Vercel AI Gateway",
        "signup_url": "https://vercel.com/ai-gateway",
        "trust": "relay",
        "note": "多供应商聚合，$5/月免费额度",
    },
    "GLAMA_API_KEY": {
        "name": "Glama",
        "signup_url": "https://glama.ai/",
        "trust": "relay",
        "note": "多供应商聚合中转站",
    },
    "AIMLAPI_API_KEY": {
        "name": "AI/ML API",
        "signup_url": "https://aimlapi.com/",
        "trust": "relay",
        "note": "多模型聚合中转站，试用额度",
    },
}


def get_provider_info(env_var: str) -> ProviderInfo:
    """拿不到目录信息时给一个通用兜底展示，保证前端不会因为漏收录而报错。"""
    return PROVIDER_CATALOG.get(env_var, {
        "name": env_var.replace("_API_KEY", "").replace("_TOKEN", "").replace("_", " ").title(),
        "signup_url": "",
        "trust": "third_party",
        "note": "",
    })
