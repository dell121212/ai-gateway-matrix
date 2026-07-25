#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
渠道额度目录（文档 + 配置合并）
————————————————————————————————————————
各家免费层限额维度不统一：常见 RPM/RPD/TPM/TPD，另有 TPH、月 tokens、并发。

数据来源（2026 公开文档，账号 tier 变化时以控制台为准）：
  · Groq:       https://console.groq.com/docs/rate-limits
  · Gemini:     https://ai.google.dev/gemini-api/docs/rate-limits
  · Cerebras:   https://inference-docs.cerebras.ai/support/rate-limits
  · OpenRouter: https://openrouter.ai/docs/api_reference/limits
  · Mistral:    https://docs.mistral.ai/admin/billing-usage/usage-limits
  · DeepSeek:   https://api-docs.deepseek.com/quick_start/rate_limit

字段：
  id          rpm / rpd / tpm / tpd / tph / rpw / month_tokens / concurrency …
  metric      requests | tokens | concurrent
  window_sec  60 / 3600 / 86400 / 604800 / 2592000 / 0(并发)
  limit       上限；None = 文档未给固定值（控制台可见）
  source      docs | config | estimated
"""

from __future__ import annotations

from typing import Any, Optional

# window_sec 常量
MIN = 60
HOUR = 3600
DAY = 86400
WEEK = 604800
MONTH = 2592000  # 30d 近似

# 公开文档参考（免费层 / 试用层保守值）
PROVIDER_QUOTAS: dict[str, dict[str, Any]] = {
    "GROQ_API_KEY": {
        "docs_url": "https://console.groq.com/docs/rate-limits",
        "note_zh": "官方按模型给出 RPM/RPD/TPM/TPD；同组织共用。下表为常见免费层量级（小模型偏高 RPD）。",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": 30, "source": "docs",
             "label_zh": "每分钟请求 (RPM)", "label_en": "Requests / minute"},
            {"id": "rpd", "metric": "requests", "window_sec": DAY, "limit": 14400, "source": "docs",
             "label_zh": "每日请求 (RPD)", "label_en": "Requests / day"},
            {"id": "tpm", "metric": "tokens", "window_sec": MIN, "limit": 6000, "source": "docs",
             "label_zh": "每分钟 tokens (TPM)", "label_en": "Tokens / minute"},
            {"id": "tpd", "metric": "tokens", "window_sec": DAY, "limit": 500000, "source": "docs",
             "label_zh": "每日 tokens (TPD)", "label_en": "Tokens / day"},
        ],
    },
    "GEMINI_API_KEY": {
        "docs_url": "https://ai.google.dev/gemini-api/docs/rate-limits",
        "note_zh": "官方维度：RPM / 输入 TPM / RPD；按项目与 usage tier。Free 层模型差异大，表中为 Flash 类常见量级。",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": 10, "source": "docs",
             "label_zh": "每分钟请求 (RPM)", "label_en": "Requests / minute"},
            {"id": "rpd", "metric": "requests", "window_sec": DAY, "limit": 250, "source": "docs",
             "label_zh": "每日请求 (RPD)", "label_en": "Requests / day"},
            {"id": "tpm", "metric": "tokens", "window_sec": MIN, "limit": 250000, "source": "docs",
             "label_zh": "每分钟 tokens (TPM)", "label_en": "Tokens / minute"},
        ],
    },
    "CEREBRAS_API_KEY": {
        "docs_url": "https://inference-docs.cerebras.ai/support/rate-limits",
        "note_zh": (
            "官网当前仍列 $0 Free API 层；Free 与 Developer 的 RPM/TPM 不同，"
            "精确数字以 cloud.cerebras.ai 控制台为准。"
        ),
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": None, "source": "docs",
             "label_zh": "每分钟请求 (RPM)", "label_en": "Requests / minute"},
            {"id": "tpm", "metric": "tokens", "window_sec": MIN, "limit": None, "source": "docs",
             "label_zh": "每分钟 tokens (TPM)", "label_en": "Tokens / minute"},
        ],
    },
    "SAMBANOVA_API_KEY": {
        "docs_url": "https://docs.sambanova.ai/cloud/docs/get-started/rate-limits",
        "note_zh": "新用户 $5 credits 为一次性试用且 30 天到期；速率以控制台 Limits 为准。",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": 50, "source": "docs",
             "label_zh": "每分钟请求 (RPM)", "label_en": "Requests / minute"},
            {"id": "rpd", "metric": "requests", "window_sec": DAY, "limit": None, "source": "docs",
             "label_zh": "每日请求 (RPD)", "label_en": "Requests / day"},
            {"id": "tpm", "metric": "tokens", "window_sec": MIN, "limit": None, "source": "docs",
             "label_zh": "每分钟 tokens (TPM)", "label_en": "Tokens / minute"},
        ],
    },
    "MISTRAL_KEY_1": {
        "docs_url": "https://docs.mistral.ai/admin/billing-usage/usage-limits",
        "note_zh": "Free 实验档：常见 Standard 池约 50K TPM + 月 tokens；控制台 Limits 页为权威。",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": 60, "source": "docs",
             "label_zh": "每分钟请求 (≈1 RPS 量级)", "label_en": "Requests / minute"},
            {"id": "tpm", "metric": "tokens", "window_sec": MIN, "limit": 50000, "source": "docs",
             "label_zh": "每分钟 tokens (TPM)", "label_en": "Tokens / minute"},
            {"id": "month_tokens", "metric": "tokens", "window_sec": MONTH, "limit": 4000000, "source": "docs",
             "label_zh": "每月 tokens", "label_en": "Tokens / month"},
        ],
    },
    "MISTRAL_KEY_2": {
        "docs_url": "https://docs.mistral.ai/admin/billing-usage/usage-limits",
        "note_zh": "同 Mistral 账号 2；限额以控制台为准。",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": 60, "source": "docs",
             "label_zh": "每分钟请求 (RPM)", "label_en": "Requests / minute"},
            {"id": "tpm", "metric": "tokens", "window_sec": MIN, "limit": 50000, "source": "docs",
             "label_zh": "每分钟 tokens (TPM)", "label_en": "Tokens / minute"},
            {"id": "month_tokens", "metric": "tokens", "window_sec": MONTH, "limit": 4000000, "source": "docs",
             "label_zh": "每月 tokens", "label_en": "Tokens / month"},
        ],
    },
    "GLM_API_KEY": {
        "docs_url": "https://open.bigmodel.cn/dev/api",
        "note_zh": "智谱按模型/套餐给 RPM·TPM；免费层常见较高 RPM。",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": 200, "source": "docs",
             "label_zh": "每分钟请求 (RPM)", "label_en": "Requests / minute"},
            {"id": "rpd", "metric": "requests", "window_sec": DAY, "limit": None, "source": "docs",
             "label_zh": "每日请求 (RPD)", "label_en": "Requests / day"},
            {"id": "tpm", "metric": "tokens", "window_sec": MIN, "limit": None, "source": "docs",
             "label_zh": "每分钟 tokens (TPM)", "label_en": "Tokens / minute"},
            {"id": "month_tokens", "metric": "tokens", "window_sec": MONTH, "limit": None, "source": "docs",
             "label_zh": "每月 tokens / 套餐", "label_en": "Tokens / month"},
        ],
    },
    "DEEPSEEK_API_KEY": {
        "docs_url": "https://api-docs.deepseek.com/quick_start/rate_limit",
        "note_zh": "官方主限制为并发（非固定 RPM/RPD）；试用额度另计。",
        "windows": [
            {"id": "concurrency", "metric": "concurrent", "window_sec": 0, "limit": 500, "source": "docs",
             "label_zh": "并发上限（官方量级）", "label_en": "Concurrency"},
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": None, "source": "docs",
             "label_zh": "每分钟请求 (无固定 RPM)", "label_en": "Requests / minute"},
            {"id": "month_tokens", "metric": "tokens", "window_sec": MONTH, "limit": None, "source": "docs",
             "label_zh": "试用/余额 tokens", "label_en": "Trial / balance tokens"},
        ],
    },
    "DASHSCOPE_API_KEY": {
        "docs_url": "https://help.aliyun.com/zh/model-studio/rate-limit",
        "note_zh": "百炼按模型 QPM/TPM；新人额度按模型独立发放，通常 30～90 天，到期不重置。",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": None, "source": "docs",
             "label_zh": "每分钟请求 (QPM)", "label_en": "Requests / minute"},
            {"id": "tpm", "metric": "tokens", "window_sec": MIN, "limit": None, "source": "docs",
             "label_zh": "每分钟 tokens (TPM)", "label_en": "Tokens / minute"},
            {"id": "rpd", "metric": "requests", "window_sec": DAY, "limit": None, "source": "docs",
             "label_zh": "每日请求", "label_en": "Requests / day"},
            {"id": "trial_tokens", "metric": "tokens", "window_sec": MONTH, "limit": None, "source": "docs",
             "label_zh": "新人一次性 tokens（以控制台为准）", "label_en": "One-time trial tokens"},
        ],
    },
    "MODELSCOPE_API_KEY": {
        "docs_url": "https://modelscope.cn/docs/model-service/API-Inference/limits",
        "note_zh": "魔搭 API-Inference：用户总量约 2000 次/日，单模型常 ≤500/日，每日 UTC+8 重置。",
        "windows": [
            {"id": "rpd", "metric": "requests", "window_sec": DAY, "limit": 2000, "source": "docs",
             "label_zh": "每日请求 (用户总量)", "label_en": "Requests / day (user)"},
            {"id": "rpd_model", "metric": "requests", "window_sec": DAY, "limit": 500, "source": "docs",
             "label_zh": "单模型每日上限(动态)", "label_en": "Per-model RPD (dynamic)"},
        ],
    },
    "CLOUDFLARE_API_TOKEN": {
        "docs_url": "https://developers.cloudflare.com/workers-ai/platform/pricing/",
        "note_zh": "Workers AI 免费约 10,000 Neurons/日（每日重置）；超额需 Paid。",
        "windows": [
            {"id": "neurons_day", "metric": "tokens", "window_sec": DAY, "limit": 10000, "source": "docs",
             "label_zh": "每日 Neurons", "label_en": "Neurons / day"},
        ],
    },
    "SILICONFLOW_API_KEY": {
        "docs_url": "https://docs.siliconflow.cn/",
        "note_zh": "硅基流动按账号/模型；免费层常见较高 RPM。",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": 100, "source": "docs",
             "label_zh": "每分钟请求 (RPM)", "label_en": "Requests / minute"},
            {"id": "rpd", "metric": "requests", "window_sec": DAY, "limit": None, "source": "docs",
             "label_zh": "每日请求 (RPD)", "label_en": "Requests / day"},
            {"id": "tpm", "metric": "tokens", "window_sec": MIN, "limit": None, "source": "docs",
             "label_zh": "每分钟 tokens (TPM)", "label_en": "Tokens / minute"},
        ],
    },
    "OPENROUTER_API_KEY": {
        "docs_url": "https://openrouter.ai/docs/api-reference/limits",
        "note_zh": "免费模型 :free：20 RPM；日限 50（购满 $10 积分后 1000 RPD）。",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": 20, "source": "docs",
             "label_zh": "每分钟请求 (RPM)", "label_en": "Requests / minute"},
            {"id": "rpd", "metric": "requests", "window_sec": DAY, "limit": 50, "source": "docs",
             "label_zh": "每日请求 (RPD·免费层)", "label_en": "Requests / day (free)"},
            {"id": "rpd_boost", "metric": "requests", "window_sec": DAY, "limit": 1000, "source": "docs",
             "label_zh": "每日请求 (购积分后)", "label_en": "Requests / day (after credits)"},
        ],
    },
    "GITHUB_TOKEN": {
        "docs_url": "https://docs.github.com/en/github-models/prototyping-with-ai-models",
        "note_zh": "GitHub Models 原型额度按账号；以下为常见量级。",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": 15, "source": "docs",
             "label_zh": "每分钟请求 (RPM)", "label_en": "Requests / minute"},
            {"id": "rpd", "metric": "requests", "window_sec": DAY, "limit": 150, "source": "docs",
             "label_zh": "每日请求 (RPD)", "label_en": "Requests / day"},
        ],
    },
    "NVIDIA_API_KEY": {
        "docs_url": "https://docs.api.nvidia.com/nim/reference/rate-limits",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": 40, "source": "docs",
             "label_zh": "每分钟请求 (RPM)", "label_en": "Requests / minute"},
            {"id": "rpd", "metric": "requests", "window_sec": DAY, "limit": None, "source": "docs",
             "label_zh": "每日请求", "label_en": "Requests / day"},
        ],
    },
    "TOGETHER_API_KEY": {
        "docs_url": "https://docs.together.ai/docs/rate-limits",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": 60, "source": "docs",
             "label_zh": "每分钟请求 (RPM)", "label_en": "Requests / minute"},
            {"id": "tpm", "metric": "tokens", "window_sec": MIN, "limit": None, "source": "docs",
             "label_zh": "每分钟 tokens (TPM)", "label_en": "Tokens / minute"},
            {"id": "rpd", "metric": "requests", "window_sec": DAY, "limit": None, "source": "docs",
             "label_zh": "每日请求", "label_en": "Requests / day"},
        ],
    },
    "FIREWORKS_API_KEY": {
        "docs_url": "https://docs.fireworks.ai/guides/rate-limits",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": 60, "source": "docs",
             "label_zh": "每分钟请求 (RPM)", "label_en": "Requests / minute"},
            {"id": "tpm", "metric": "tokens", "window_sec": MIN, "limit": None, "source": "docs",
             "label_zh": "每分钟 tokens (TPM)", "label_en": "Tokens / minute"},
            {"id": "rpd", "metric": "requests", "window_sec": DAY, "limit": None, "source": "docs",
             "label_zh": "每日请求", "label_en": "Requests / day"},
        ],
    },
    "MOONSHOT_API_KEY": {
        "docs_url": "https://platform.moonshot.cn/docs/pricing/limits",
        "note_zh": "Kimi 试用常见低 RPM；以控制台为准。",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": 3, "source": "docs",
             "label_zh": "每分钟请求 (试用常见)", "label_en": "Requests / minute"},
            {"id": "tpm", "metric": "tokens", "window_sec": MIN, "limit": None, "source": "docs",
             "label_zh": "每分钟 tokens (TPM)", "label_en": "Tokens / minute"},
            {"id": "rpd", "metric": "requests", "window_sec": DAY, "limit": None, "source": "docs",
             "label_zh": "每日请求", "label_en": "Requests / day"},
            {"id": "month_tokens", "metric": "tokens", "window_sec": MONTH, "limit": None, "source": "docs",
             "label_zh": "每月额度", "label_en": "Monthly quota"},
        ],
    },
    "AGNES_API_KEY": {
        "docs_url": "https://platform.agnes-ai.com/",
        # 详情只在 docs 链里；看板只展示主周期 RPM，避免图片/视频多行噪音
        "note_zh": "文本 Effective RPM≈20/分（聊天主路径）。图/视频另有更低限额，见文档。",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": 20, "source": "docs",
             "label_zh": "当前周期（每分钟）", "label_en": "Current period (per minute)"},
        ],
    },
    "GENERALCOMPUTE_API_KEY": {
        "docs_url": "https://app.generalcompute.com/",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": None, "source": "docs",
             "label_zh": "每分钟请求（按账号）", "label_en": "Requests / minute"},
            {"id": "month_budget", "metric": "tokens", "window_sec": MONTH, "limit": None, "source": "docs",
             "label_zh": "按量余额 / 月", "label_en": "Balance / month"},
        ],
    },
    "HF_TOKEN": {
        "docs_url": "https://huggingface.co/docs/api-inference/rate-limits",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": 10, "source": "docs",
             "label_zh": "每分钟请求 (RPM)", "label_en": "Requests / minute"},
            {"id": "rpd", "metric": "requests", "window_sec": DAY, "limit": None, "source": "docs",
             "label_zh": "每日请求", "label_en": "Requests / day"},
        ],
    },
    "DEEPINFRA_API_KEY": {
        "docs_url": "https://deepinfra.com/docs/advanced/rate-limits",
        "note_zh": "当前按量付费，账户需保持正余额；未承诺稳定免费层。",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": 30, "source": "docs",
             "label_zh": "每分钟请求 (RPM)", "label_en": "Requests / minute"},
            {"id": "tpm", "metric": "tokens", "window_sec": MIN, "limit": None, "source": "docs",
             "label_zh": "每分钟 tokens", "label_en": "Tokens / minute"},
        ],
    },
    "NOVITA_API_KEY": {
        "docs_url": "https://novita.ai/docs",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": None, "source": "docs",
             "label_zh": "每分钟请求", "label_en": "Requests / minute"},
            {"id": "rpd", "metric": "requests", "window_sec": DAY, "limit": None, "source": "docs",
             "label_zh": "每日请求", "label_en": "Requests / day"},
        ],
    },
    "LEPTON_API_KEY": {
        "docs_url": "https://www.lepton.ai/docs",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": None, "source": "docs",
             "label_zh": "每分钟请求", "label_en": "Requests / minute"},
        ],
    },
    "HUNYUAN_API_KEY": {
        "docs_url": "https://cloud.tencent.com/document/product/1729",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": None, "source": "docs",
             "label_zh": "每分钟请求 (QPM)", "label_en": "Requests / minute"},
            {"id": "rpd", "metric": "requests", "window_sec": DAY, "limit": None, "source": "docs",
             "label_zh": "每日请求", "label_en": "Requests / day"},
            {"id": "month_tokens", "metric": "tokens", "window_sec": MONTH, "limit": 1000000, "source": "docs",
             "label_zh": "每月免费 tokens（常见）", "label_en": "Monthly free tokens"},
        ],
    },
    "QIANFAN_API_KEY": {
        "docs_url": "https://cloud.baidu.com/doc/WENXINWORKSHOP/s/Nlks5zkzu",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": None, "source": "docs",
             "label_zh": "每分钟请求 (QPS/QPM)", "label_en": "Requests / minute"},
            {"id": "rpd", "metric": "requests", "window_sec": DAY, "limit": None, "source": "docs",
             "label_zh": "每日请求", "label_en": "Requests / day"},
            {"id": "month_tokens", "metric": "tokens", "window_sec": MONTH, "limit": None, "source": "docs",
             "label_zh": "每月额度", "label_en": "Monthly quota"},
        ],
    },
    "AIHUBMIX_API_KEY": {
        "docs_url": "https://aihubmix.com/",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": None, "source": "docs",
             "label_zh": "每分钟请求", "label_en": "Requests / minute"},
            {"id": "rpd", "metric": "requests", "window_sec": DAY, "limit": None, "source": "docs",
             "label_zh": "每日请求", "label_en": "Requests / day"},
            {"id": "month_budget", "metric": "tokens", "window_sec": MONTH, "limit": None, "source": "docs",
             "label_zh": "余额 / 月额度", "label_en": "Balance / month"},
        ],
    },
    "VERCEL_AI_API_KEY": {
        "docs_url": "https://vercel.com/docs/ai-gateway",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": None, "source": "docs",
             "label_zh": "每分钟请求", "label_en": "Requests / minute"},
            {"id": "month_budget", "metric": "tokens", "window_sec": MONTH, "limit": None, "source": "docs",
             "label_zh": "每月额度（常有 $5 试用）", "label_en": "Monthly credit (often $5)"},
        ],
    },
    "GLAMA_API_KEY": {
        "docs_url": "https://glama.ai/gateway",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": None, "source": "docs",
             "label_zh": "每分钟请求", "label_en": "Requests / minute"},
            {"id": "rpd", "metric": "requests", "window_sec": DAY, "limit": None, "source": "docs",
             "label_zh": "每日请求", "label_en": "Requests / day"},
        ],
    },
    "AIMLAPI_API_KEY": {
        "docs_url": "https://docs.aimlapi.com/",
        "windows": [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": None, "source": "docs",
             "label_zh": "每分钟请求", "label_en": "Requests / minute"},
            {"id": "rpd", "metric": "requests", "window_sec": DAY, "limit": None, "source": "docs",
             "label_zh": "每日请求", "label_en": "Requests / day"},
        ],
    },
}


def _window_label(window_sec: int) -> str:
    if window_sec == 0:
        return "并发"
    if window_sec <= MIN:
        return "分钟"
    if window_sec <= HOUR:
        return "小时"
    if window_sec <= DAY:
        return "天"
    if window_sec <= WEEK:
        return "周"
    return "月"


def _merge_auto_refresh(env_key: str, entry: dict[str, Any]) -> dict[str, Any]:
    """合并 state/free-tier-quotas.json（定时跟进的最新限额）。"""
    try:
        from gateway.free_tier_refresh import get_provider_override
        over = get_provider_override(env_key)
    except Exception:
        over = None
    if not over:
        return entry
    merged = dict(entry or {})
    if over.get("note_zh"):
        merged["note_zh"] = over["note_zh"]
    if over.get("docs_url"):
        merged["docs_url"] = over["docs_url"]
    # 有 windows 时覆盖（保留 source 标记为 auto）
    owin = over.get("windows")
    if isinstance(owin, list) and owin:
        merged["windows"] = [dict(w) for w in owin]
        merged["auto_refresh"] = {
            "checked_at": over.get("checked_at"),
            "confidence": over.get("confidence"),
            "free_kind": over.get("free_kind"),
            "source": over.get("source"),
        }
    return merged


def build_rate_limits(
    env_var: Optional[str],
    config_rpm: Optional[int] = None,
    usage: Optional[dict] = None,
    quota_kind: Optional[str] = None,
) -> dict[str, Any]:
    """合并文档限额 + 自动跟进 + config rpm + 运行时用量。"""
    usage = usage or {}
    env_key = env_var or ""
    entry = PROVIDER_QUOTAS.get(env_key, {})
    if not entry and env_key:
        # FOO_API_KEY_2 → FOO_API_KEY / FOO_API_KEY_1
        import re as _re
        m = _re.match(r"^(.*)_(\d+)$", env_key)
        if m:
            base = m.group(1)
            entry = PROVIDER_QUOTAS.get(base) or PROVIDER_QUOTAS.get(f"{base}_1") or {}
    entry = _merge_auto_refresh(env_key, entry if isinstance(entry, dict) else {})
    windows = [dict(w) for w in entry.get("windows") or []]

    # config.yaml 的 rpm 覆盖文档 RPM（本机调度更贴合）
    if config_rpm is not None:
        found = False
        for w in windows:
            if w.get("id") == "rpm" and w.get("metric") == "requests":
                w["limit"] = int(config_rpm)
                w["source"] = "config"
                found = True
                break
        if not found:
            windows.insert(0, {
                "id": "rpm",
                "metric": "requests",
                "window_sec": MIN,
                "limit": int(config_rpm),
                "source": "config",
                "label_zh": "每分钟请求 (RPM·config)",
                "label_en": "Requests / minute (config)",
            })

    if not windows and config_rpm is not None:
        windows = [{
            "id": "rpm",
            "metric": "requests",
            "window_sec": MIN,
            "limit": int(config_rpm),
            "source": "config",
            "label_zh": "每分钟请求 (RPM)",
            "label_en": "Requests / minute",
        }]

    # 若仍无文档，至少给出分钟/天/月骨架，便于 UI「显示全」
    if not windows:
        windows = [
            {"id": "rpm", "metric": "requests", "window_sec": MIN, "limit": config_rpm,
             "source": "config" if config_rpm is not None else "estimated",
             "label_zh": "每分钟请求 (RPM)", "label_en": "Requests / minute"},
            {"id": "rpd", "metric": "requests", "window_sec": DAY, "limit": None,
             "source": "estimated", "label_zh": "每日请求 (RPD)", "label_en": "Requests / day"},
            {"id": "tpm", "metric": "tokens", "window_sec": MIN, "limit": None,
             "source": "estimated", "label_zh": "每分钟 tokens (TPM)", "label_en": "Tokens / minute"},
            {"id": "month_tokens", "metric": "tokens", "window_sec": MONTH, "limit": None,
             "source": "estimated", "label_zh": "每月 tokens", "label_en": "Tokens / month"},
        ]

    for w in windows:
        used = None
        reset = None
        tracked = False
        metric = w.get("metric")
        wsec = int(w.get("window_sec") or 0)
        if usage.get("available"):
            if metric == "requests" and wsec == MIN:
                used = usage.get("calls_this_minute")
                reset = usage.get("seconds_until_minute_reset")
                tracked = used is not None
            elif metric == "requests" and wsec == DAY:
                used = usage.get("calls_today")
                reset = usage.get("seconds_until_day_reset")
                tracked = used is not None
            elif metric == "tokens" and wsec == DAY:
                used = usage.get("day_tokens")
                reset = usage.get("seconds_until_day_reset")
                tracked = used is not None
            elif metric == "tokens" and wsec == MIN:
                used = usage.get("minute_tokens")
                reset = usage.get("seconds_until_minute_reset")
                tracked = used is not None
            elif metric == "tokens" and wsec == MONTH:
                if quota_kind == "once" or w.get("id") == "trial_tokens":
                    used = usage.get("total_tokens")
                    reset = None
                    tracked = used is not None
                else:
                    used = usage.get("month_tokens")
                    reset = usage.get("seconds_until_month_reset")
                    tracked = used is not None
                    w["usage_estimated"] = bool(usage.get("month_tokens_estimated"))
        w["used"] = used if tracked else None
        w["seconds_until_reset"] = reset if tracked else None
        w["usage_tracked"] = tracked
        w["window_label"] = (
            "累计"
            if metric == "tokens" and wsec == MONTH
            and (quota_kind == "once" or w.get("id") == "trial_tokens")
            else _window_label(wsec)
        )

    note_zh = entry.get("note_zh") or (
        "限额来自公开文档与本机 config；账号档位变化时以官方控制台为准。"
        "本机实时统计分钟/日请求，以及分钟/日/月/累计 tokens。"
    )
    auto = entry.get("auto_refresh") if isinstance(entry.get("auto_refresh"), dict) else None
    if auto and auto.get("checked_at"):
        extra = f"自动跟进 {auto.get('checked_at')}"
        try:
            if auto.get("confidence") is not None:
                extra += f" · 置信 {float(auto['confidence']):.0%}"
        except (TypeError, ValueError):
            pass
        if auto.get("free_kind"):
            extra += f" · {auto['free_kind']}"
        note_zh = f"{note_zh}（{extra}）"

    summary = summarize_primary_quota(windows, usage)
    return {
        "docs_url": entry.get("docs_url") or "",
        "windows": windows,
        "summary": summary,
        "note_zh": note_zh,
        "note_en": entry.get("note_en") or (
            "Limits merge public docs with local config; account tier may differ. "
            "Live usage tracks minute/day requests and minute/day/month/lifetime tokens."
        ),
        "auto_refresh": auto,
    }


def summarize_primary_quota(
    windows: list[dict[str, Any]],
    usage: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """提炼用户真正关心的两行：当前周期剩余% + 总 token 消耗。

    周期优先级：日请求(RPD) > 月 tokens > 日 tokens > 分钟 RPM（免费层常见）。
    无 limit 的窗口只展示已用量，不算百分比。
    """
    usage = usage or {}
    candidates = [w for w in windows if isinstance(w, dict)]
    preferred_ids = ("rpd", "month_tokens", "tpd", "rpm", "tpm")
    primary: Optional[dict[str, Any]] = None
    for pid in preferred_ids:
        for w in candidates:
            if w.get("id") == pid and w.get("limit") not in (None, 0, ""):
                try:
                    if float(w["limit"]) > 0:
                        primary = w
                        break
                except (TypeError, ValueError):
                    continue
        if primary is not None:
            break
    if primary is None:
        for w in candidates:
            try:
                if w.get("limit") is not None and float(w["limit"]) > 0:
                    primary = w
                    break
            except (TypeError, ValueError):
                continue

    remaining_pct: Optional[float] = None
    used = None
    limit = None
    period_label = "当前周期"
    if primary is not None:
        period_label = str(primary.get("label_zh") or primary.get("window_label") or "当前周期")
        try:
            limit = float(primary["limit"])
            used_raw = primary.get("used")
            if used_raw is None and usage.get("available"):
                # 兜底：按窗口类型从 usage 取
                wsec = int(primary.get("window_sec") or 0)
                metric = primary.get("metric")
                if metric == "requests" and wsec == MIN:
                    used_raw = usage.get("calls_this_minute")
                elif metric == "requests" and wsec == DAY:
                    used_raw = usage.get("calls_today")
                elif metric == "tokens" and wsec == DAY:
                    used_raw = usage.get("day_tokens")
                elif metric == "tokens" and wsec == MONTH:
                    used_raw = usage.get("month_tokens") or usage.get("total_tokens")
            if used_raw is not None and limit > 0:
                used = float(used_raw)
                remaining_pct = max(0.0, min(100.0, round((1.0 - used / limit) * 100.0, 1)))
        except (TypeError, ValueError):
            remaining_pct = None

    total_tokens = usage.get("total_tokens") if usage.get("available") else None
    day_tokens = usage.get("day_tokens") if usage.get("available") else None

    return {
        "period_label": period_label,
        "remaining_pct": remaining_pct,
        "used": used,
        "limit": limit,
        "total_tokens": total_tokens,
        "day_tokens": day_tokens,
        "seconds_until_reset": (primary or {}).get("seconds_until_reset"),
    }
