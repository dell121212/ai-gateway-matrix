#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
复杂度启发式路由 Hook (v4 — 引入指定模型分类)
————————————————————————————————————————
挂载在 LiteLLM 的 async_pre_call_hook 上：每次请求真正发给某个渠道之前，
先看一眼任务内容，决定改写 data["model"] 为 "fast-pool" / "free-pool" /
"strong-model-pool" / "trusted-pool" 之一。

设计原则（智能模式 = 两段式）：
  · **先用强模型快速判断提问强度**（gateway/llm_classifier.py），
    再 **内部决定** 用弱/中/强池里的哪个模型真正回答。
  · 敏感内容检测与「极短输入直接 fast-pool」仍是纯规则（零延迟）；
    其余正常提问走强模型分诊。
  · decide_pool() 启发式完整保留，仅作分诊失败时的兜底。
  · 池内具体渠道由 LiteLLM Router（simple-shuffle 等）选择。
  · 与 context_window_fallbacks 互补：超长上下文硬升 strong。

══════════════════════════════════════════════════════════════════════
v3 新增（安全加固）：
  1. [CRITICAL] 新增敏感内容检测 _detect_sensitive()——在做任何"要不要
     升级复杂度"的判断之前，先检查 prompt 里是否疑似包含 API Key、密码、
     私钥、数据库连接串、内网地址等。命中就无条件路由到 trusted-pool
     （只含官方直营渠道，明确不包含 Agnes AI 这类上线仅一个月、
     靠自建榜单营销的新渠道，也不含任何中转站/聚合站），即使同时命中了
     "升级到 strong-model-pool" 的关键词也会被这条规则覆盖。
     理由：config.yaml 里 strong-model-pool 混了不少第三方推理托管/试用
     账号渠道，而"重构""安全审计""数据库设计"这类触发升级的关键词，
     恰恰最容易伴随真实的敏感信息出现——性能路由不应该凌驾于数据安全之上。
  2. 敏感信息只记录"命中了哪一类模式"（如 "aws_key"），不记录匹配到的
     具体内容本身，避免日志里又存了一份泄漏的密钥。
  3. 新增 stats 计数器 routed_to_trusted_sensitive，方便观察触发频率。

══════════════════════════════════════════════════════════════════════
v2 修复清单（对照 Manus 验证报告 + LiteLLM 1.90.1 源码深挖）：
  1. [CRITICAL] 修复 call_type 判断 —— LiteLLM Proxy 对
     /v1/chat/completions 传入的 call_type 是 "acompletion"（异步）而非
     "completion"（同步）。原版 `if call_type not in ("completion",
     "text_completion")` 永远为 True，导致 hook 直接 return、复杂度路由
     逻辑根本没执行。现在显式覆盖所有补全类 call_type。
  2. [CRITICAL] 修复 async_pre_call_hook 签名 —— LiteLLM 1.90.x 的
     async_pre_call_hook 签名是 (self, user_api_key_dict, cache, data,
     call_type)，原版缺少 cache 参数会导致 TypeError。
  3. 新增 _extract_text() 容错：处理 messages 里 content 为 list（多模态）
     或 None 的情况，原版直接 str(content) 会把 list 变成 "[{...}]" 字符串。
  4. 新增 _estimate_token_count() 粗估 token 数，用于在没有精确 tokenizer
     时做长度判断（4 字符 ≈ 1 token 的经验值）。
  5. 新增 ESCALATE_PATTERNS 正则匹配，覆盖"超过 N 个文件""超过 N 行"等
     结构化判断，比纯关键词更精准。
  6. async_post_call_failure_hook 增加 user_api_key_dict 参数（1.90.x
     签名变更），原版缺这个参数会报 TypeError。
  7. 新增 _classify_and_log_error() 统一错误分类逻辑，覆盖更多错误码。
  8. 新增 stats 计数器，记录升级/降级/错误次数，方便调试。
══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import ast
import asyncio
import logging
import os
import re
from typing import Any, Optional

import litellm
from litellm.integrations.custom_logger import CustomLogger

from . import (
    answer_verifier,
    env_sync,
    llm_classifier,
    optimal_channels,
    pricing,
    provider_registry,
    quota_manager,
    usage_tracker,
)

logger = logging.getLogger("ai_gateway_matrix.router_hook")

# 网关进程启动后台热加载：仪表盘改 Key/配置后无需 bash run.sh
try:
    env_sync.start_background_watcher()
except Exception:
    pass

FAST_POOL = "fast-pool"              # 弱 0.5B–8B
FREE_POOL = "free-pool"              # 中 9B–30B
STRONG_POOL = "strong-model-pool"    # 强 31B–100B
ELITE_POOL = "elite-model-pool"      # 顶级 100B+
TRUSTED_POOL = "trusted-pool"
AUTO_ROUTE = "auto-route"
ALL_CAPABILITY_POOLS = (FAST_POOL, FREE_POOL, STRONG_POOL, ELITE_POOL)
POOL_RANK = {
    FAST_POOL: 0,
    FREE_POOL: 1,
    STRONG_POOL: 2,
    ELITE_POOL: 3,
}

# 客户端「模式」别名 → 内部路由目标
# 智能：按提问选档；弱/中/强：只使用该档位（敏感内容仍强制 trusted）
MODE_INTELLIGENT = frozenset({
    AUTO_ROUTE,
    "mode-intelligent",
    "intelligent",
    "smart",
    "智能",
})
MODE_WEAK = frozenset({
    FAST_POOL,
    "mode-weak",
    "weak",
    "weak-route",
    "tier-weak",
    "弱",
})
MODE_MID = frozenset({
    FREE_POOL,
    "mode-mid",
    "mode-medium",
    "mid",
    "medium",
    "mid-route",
    "tier-mid",
    "中",
})
MODE_STRONG = frozenset({
    STRONG_POOL,
    "mode-strong",
    "strong",
    "strong-route",
    "tier-strong",
    "强",
})
MODE_ELITE = frozenset({
    ELITE_POOL,
    "mode-elite",
    "elite",
    "elite-route",
    "tier-elite",
    "top",
    "顶级",
})
# LiteLLM model_list 里必须注册的别名（见 config.yaml / runtime_launcher）
PUBLIC_MODE_ALIASES = (
    "mode-intelligent",
    "mode-weak",
    "mode-mid",
    "mode-strong",
    "mode-elite",
)

# ──────────────────────────────────────────────────────────────
#  敏感内容检测（v3 新增）
#  命中任意一条 → 无条件路由到 trusted-pool，优先级高于复杂度升级规则。
#  只用来"匹配是否存在"，不用来提取/回显具体的密钥内容。
# ──────────────────────────────────────────────────────────────
SENSITIVE_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = [
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    # sk- 开头的 OpenAI/Anthropic 风格密钥（长度较长，避免误伤普通单词）
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    # 形如 xxxxx.yyyyy.zzzzz 的 JWT
    ("jwt_token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    # 数据库连接串
    ("db_connection_string", re.compile(
        r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|jdbc:[a-z]+)://[^\s\"']+", re.IGNORECASE
    )),
    # password = "xxx" / pwd: 'xxx' 这类赋值
    ("password_assignment", re.compile(
        r"(?:password|passwd|pwd|secret|api[_-]?key)\s*[:=]\s*[\"']?[^\s\"']{6,}", re.IGNORECASE
    )),
    # 内网 IP 段（10.x / 172.16-31.x / 192.168.x）
    ("internal_ip", re.compile(
        r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b"
    )),
    # .internal / .corp 这类明显的内网域名
    ("internal_hostname", re.compile(r"\b[\w-]+\.(?:internal|corp|intranet)\b", re.IGNORECASE)),
]

# 命中即升级的关键词（中英混合，按你的实际任务类型增删）
ESCALATE_KEYWORDS = [
    # 中文
    "重构", "refactor", "整个项目", "整体架构", "debug整体", "架构设计",
    "迁移", "migrate", "性能优化", "安全审计", "全量", "批量处理",
    "复杂逻辑", "深度分析", "代码审查", "code review", "技术方案",
    "代码分析", "故障排查", "根因分析", "修复建议", "死锁", "竞态条件",
    "内存泄漏", "性能瓶颈", "并发问题", "debug", "deadlock", "race condition",
    "root cause", "troubleshoot",
    "数据库设计", "系统设计", "分布式", "微服务", "高并发",
    # 英文
    "refactor", "architecture", "migration", "optimize", "security audit",
    "full project", "entire codebase", "complex logic", "deep analysis",
    "system design", "database design", "distributed", "microservice",
    "high concurrency", "performance tuning",
]

# 正则模式：比纯关键词更精准的结构化判断
ESCALATE_PATTERNS = [
    # 超过 N 个文件
    re.compile(r"(?:超过|over|more than)\s*(\d+)\s*(?:个文件|files)", re.IGNORECASE),
    # 超过 N 行代码
    re.compile(r"(?:超过|over|more than)\s*(\d+)\s*(?:行|lines)", re.IGNORECASE),
    # 超过 N 个函数/类
    re.compile(r"(?:超过|over|more than)\s*(\d+)\s*(?:个函数|functions|个类|classes)", re.IGNORECASE),
]

# 触发升级的阈值（弱→中→强→顶级）
TOKEN_THRESHOLD = 8000         # ≥ 此 → 强档
ELITE_TOKEN_THRESHOLD = 24000  # ≥ 此 → 顶级
CHAR_THRESHOLD = 30000         # ≥ 此 → 强档
ELITE_CHAR_THRESHOLD = 90000   # ≥ 此 → 顶级
FILE_COUNT_THRESHOLD = 5       # 提到超过 N 个文件则升级到强

# 路由到弱档的阈值
FAST_TOKEN_THRESHOLD = 200   # 粗估 token 数低于此值 → 弱档
FAST_CHAR_THRESHOLD = 600    # 字符数低于此值 → 弱档

# 短并不等于简单。只有明确的寒暄、算术或提取/分类任务才允许直接落到弱档；
# 写作、翻译和普通编程至少用中档。代码诊断类由 ESCALATE_KEYWORDS 直接升强。
TRIVIAL_TASK_PATTERNS = (
    re.compile(r"^\s*(?:hi|hello|hey|你好|您好|嗨|在吗)[!！,.，。?？\s]*$", re.IGNORECASE),
    re.compile(r"^\s*(?:请)?(?:计算|算一下|求值)?\s*[\d\s()+\-*/%.]+(?:等于多少|是多少)?[?？\s]*$"),
    re.compile(r"^\s*(?:请)?(?:判断|分类|提取|识别).{0,80}$", re.IGNORECASE | re.DOTALL),
)

MID_FLOOR_PATTERNS = (
    re.compile(r"(?:写一|写个|撰写|改写|润色|扩写|文案|邮件|总结|翻译|translate|rewrite|polish)", re.IGNORECASE),
    re.compile(
        r"(?:写代码|编程|函数|脚本|正则|sql|python|javascript|typescript|java|golang|rust|"
        r"api\b|algorithm|implement|code\b)",
        re.IGNORECASE,
    ),
)

QUALITY_GUARDED_MODELS = ("qwen2.5-7b-instruct",)
QUALITY_GUARDED_BASES = ("api.siliconflow.cn",)
# 所有模式（智能/弱/中/强/顶级）均拒绝下列坏输出，并触发同档 peer 重试
UNIVERSAL_QUALITY_FAILURES = {
    "empty_output",
    "chat_template_echo",
    "mojibake",
    "arithmetic_mismatch",
    "repeated_lines",
    "prompt_echo",
    "upstream_error_text",
    "upstream_error_json",
    "html_error_page",
    "llm_reject",
    "too_short",
    "low_entropy",
    "repeat_chunks",
}

# 上游常把 4xx/5xx 文案塞进 200 正文
_UPSTREAM_ERROR_BODY = re.compile(
    r"(?:"
    r"rate\s*limit|too many requests|quota\s*(?:exceeded|exhausted)|"
    r"capacity|overloaded|service\s*unavailable|internal\s*server\s*error|"
    r"model\s*(?:is\s*)?(?:not\s*found|unavailable|does not exist)|"
    r"invalid\s*api\s*key|unauthorized|permission\s*denied|access\s*denied|"
    r"context\s*length|maximum\s*context|token\s*limit|"
    r"无可用|额度不足|限流|负载已满|请求过于频繁|服务暂不可用|系统繁忙|"
    r"currently\s*unavailable|try\s*again\s*later|bad\s*gateway|gateway\s*timeout"
    r")",
    re.IGNORECASE,
)
_ERROR_JSON_HINT = re.compile(
    r"""(?x)
    \{\s*["'](?:error|message|detail|code)["']\s*:
    |["']error["']\s*:\s*\{
    |["']type["']\s*:\s*["'](?:invalid_request_error|api_error|rate_limit_error)
    """,
    re.IGNORECASE,
)


class ComplexityRouterHook(CustomLogger):
    """复杂度启发式路由 Hook。

    挂在 LiteLLM 的 async_pre_call_hook 上，在请求真正发给某个渠道之前，
    根据任务内容决定改写 data["model"] 为 "free-pool" 还是 "strong-model-pool"。
    """

    def __init__(self) -> None:
        super().__init__()
        # 简单的统计计数器，方便调试和监控
        self._stats = {
            "total_requests": 0,
            "routed_to_fast": 0,
            "routed_to_free": 0,
            "escalated_to_strong": 0,
            "routed_to_trusted_sensitive": 0,
            "routed_to_optimal": 0,
            "classifier_used": 0,
            "classifier_fallback_to_heuristic": 0,
            "classifier_skipped_trivial": 0,
            "non_completion_skipped": 0,
            "stream_forced_off": 0,
            "errors": 0,
        }
        # "限时优先"功能（v6）需要知道每个渠道的 rpm 上限和它对应的
        # direct-xxxxxxxxxx model_name，这些信息只存在于 config.yaml，
        # 启动时解析一次、缓存在内存里，避免每次请求都重新读文件。
        # 解析失败（文件不存在/格式不对）不应该让整个网关起不来，
        # 只是让"限时优先"功能自动失效，退回正常的池子路由。
        self._channel_registry: dict[str, dict] = {}
        self._provider_registry: Optional[provider_registry.ProviderRegistry] = None
        self._registry_config_mtime_ns: Optional[int] = None
        self._load_channel_registry()

    def _load_channel_registry(self) -> None:
        """解析 config.yaml，为 fast/free/strong-model-pool 里的每个 deployment
        建立 display_id -> {model, api_base, env_var, rpm_limit, direct_model_name}
        的映射，供"限时优先"功能查找用。"""
        try:
            self._provider_registry = provider_registry.load_registry()
            self._channel_registry = dict(self._provider_registry.channels)
            try:
                self._registry_config_mtime_ns = (
                    self._provider_registry.config_path.stat().st_mtime_ns
                )
            except OSError:
                self._registry_config_mtime_ns = None
        except Exception as exc:
            logger.warning(
                "[ai-gateway-matrix] 无法加载供应商注册表（%s: %s），"
                "能力过滤/限时优先将不可用",
                type(exc).__name__, exc,
            )
            return
        logger.info("[ai-gateway-matrix] 渠道注册表加载完成，共 %d 个渠道", len(self._channel_registry))

    def _ensure_channel_registry_fresh(self) -> None:
        """Reload the full catalog after a dashboard config edit.

        Key-only changes are read from the hot-synchronised environment by
        ``_is_configured``.  Model, pool, priority and trust-policy changes live
        in the source YAML and require rebuilding the cached registry.
        """
        registry = self._provider_registry
        config_path = getattr(registry, "config_path", None)
        if config_path is None:
            return
        try:
            current_mtime_ns = config_path.stat().st_mtime_ns
        except OSError:
            return
        if current_mtime_ns != self._registry_config_mtime_ns:
            self._load_channel_registry()

    # ──────────────────────────────────────────────────────────────
    #  核心路由逻辑
    # ──────────────────────────────────────────────────────────────

    def _extract_text(self, data: dict) -> str:
        """从请求 data 里把所有用户/系统消息拼成一段纯文本。

        容错处理：
          · messages 不存在或为空 → 返回空字符串
          · content 为 None → 跳过
          · content 为 list（多模态格式）→ 提取其中的 text 部分
          · content 为 str → 直接使用
        """
        parts: list[str] = []
        prompt = data.get("prompt")
        if isinstance(prompt, str):
            parts.append(prompt)
        elif isinstance(prompt, list):
            parts.extend(item for item in prompt if isinstance(item, str))

        input_value = data.get("input")
        if isinstance(input_value, str):
            parts.append(input_value)

        messages = data.get("messages") or []
        if not isinstance(messages, list):
            return "\n".join(parts)
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if content is None:
                continue
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                # 多模态格式：[{"type": "text", "text": "..."}, {"type": "image_url", ...}]
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        if isinstance(text, str):
                            parts.append(text)
            elif isinstance(content, dict):
                # 某些客户端会把 content 包成 {"text": "..."}
                text = content.get("text", "")
                if isinstance(text, str):
                    parts.append(text)

        return "\n".join(parts)

    def _estimate_token_count(self, text: str) -> int:
        """粗估 token 数。

        优先用 litellm 内置的 token_counter（如果可用），
        否则用"4 字符 ≈ 1 token"的经验值。
        """
        if not text:
            return 0
        try:
            return litellm.token_counter(text=text)
        except Exception:
            # 经验值：英文约 4 字符/token，中文约 2 字符/token，取中间值
            return len(text) // 4

    def _count_file_mentions(self, text: str) -> int:
        """粗略统计文本里提到的文件数量。

        匹配常见模式：
          · path/to/file.py
          · file.py, file2.py
          · "src/main.py"
        """
        # 匹配带扩展名的文件路径
        file_pattern = re.compile(
            r'(?:[\w./-]+/)?[\w-]+\.(?:py|js|ts|tsx|jsx|java|go|rs|c|cpp|h|rb|php|swift|kt|scala|sh|yaml|yml|json|xml|md|sql|vue|svelte)',
            re.IGNORECASE,
        )
        matches = file_pattern.findall(text)
        return len(set(matches))  # 去重

    def _detect_sensitive(self, text: str) -> Optional[str]:
        """检测文本里是否疑似包含敏感信息（API Key/密码/内网地址等）。

        只返回命中的类别名（如 "aws_access_key"），不返回、也不记录匹配到
        的具体内容——这类日志本身就是一种泄漏。命中即应无条件路由到
        trusted-pool，优先级高于任何"复杂度升级"规则。
        """
        for category, pattern in SENSITIVE_PATTERNS:
            if pattern.search(text):
                return category
        return None

    def _quick_escalation_check(self, text: str, text_lower: str) -> Optional[str]:
        """零成本快速判断：关键词/正则模式/文件数命中 → 直接升级到 strong-model-pool。

        这三条规则的共同点是"信号很明确、判断很便宜"，所以不管文本长短，
        不管分类器可不可用，都应该在最前面就短路掉——包括那种很短但
        明确写了"重构""refactor"的请求（比如"帮我重构这个项目"只有
        8 个字符，但意图很明确，不应该被"短文本→fast-pool"的规则抢先命中）。
        """
        # 规则 1：关键词命中 → 直接升级到 strong-model-pool
        for kw in ESCALATE_KEYWORDS:
            if kw.lower() in text_lower:
                logger.info(
                    "[ai-gateway-matrix] 升级到 strong-model-pool（命中关键词: %s）", kw
                )
                return STRONG_POOL

        # 规则 2：正则模式命中 → 升级到 strong-model-pool
        for pattern in ESCALATE_PATTERNS:
            if pattern.search(text):
                logger.info(
                    "[ai-gateway-matrix] 升级到 strong-model-pool（命中模式: %s）",
                    pattern.pattern,
                )
                return STRONG_POOL

        # 规则 3：文件数量超过阈值 → 升级到 strong-model-pool
        file_count = self._count_file_mentions(text)
        if file_count >= FILE_COUNT_THRESHOLD:
            logger.info(
                "[ai-gateway-matrix] 升级到 strong-model-pool（提到 %d 个文件，超过阈值 %d）",
                file_count, FILE_COUNT_THRESHOLD
            )
            return STRONG_POOL

        return None

    @staticmethod
    def _is_trivial_task(text: str) -> bool:
        return any(pattern.search(text) for pattern in TRIVIAL_TASK_PATTERNS)

    @staticmethod
    def _minimum_pool_for_text(text: str) -> Optional[str]:
        """返回任务不可被分类器降穿的档位下限；无明确下限时返回 None。"""
        if any(pattern.search(text) for pattern in MID_FLOOR_PATTERNS):
            return FREE_POOL
        return None

    @staticmethod
    def _higher_pool(first: str, second: Optional[str]) -> str:
        if second is None:
            return first
        return second if POOL_RANK.get(second, -1) > POOL_RANK.get(first, -1) else first

    def decide_pool(self, data: dict) -> str:
        """根据请求内容决定路由到哪个池子（纯规则版本）。

        返回 "fast-pool" / "free-pool" / "strong-model-pool" / "trusted-pool"。
        纯规则、零延迟、零成本——不额外发 LLM 调用。这是 decide_pool_with_classifier()
        在分类器不可用时的兜底路径，也是 scripts/test_gateway.py 离线结构性自检的对象。

        四层路由策略（按判断优先级排列，前面的规则覆盖后面的）：
          · trusted-pool:        疑似含敏感信息 → 只走官方直营渠道，
                                  隐私优先于复杂度/性能判断
          · strong-model-pool:   关键词/正则/文件数命中，或长度超阈值 → 大模型
          · fast-pool:           超短输入 + 简单任务 → Groq/Cerebras 超快推理
          · free-pool:           常规任务 → GLM/Gemini/SiliconFlow 等免费渠道
        """
        text = self._extract_text(data)
        if not text:
            return FAST_POOL  # 空输入走快速池

        # 规则 0：疑似含敏感信息 → 无条件路由到 trusted-pool，
        # 覆盖后面所有"要不要升级到 strong-model-pool"的判断。
        # 原因很直接：strong-model-pool 里混了不少第三方推理托管/试用账号/
        # 观察期渠道（比如刚上线一个月的 Agnes AI），而恰恰是"重构""安全审计"
        # "数据库设计"这类会触发升级的关键词，最容易伴随真实的密钥、内网地址
        # 一起出现。宁可牺牲一点路由"智能"，也不能让敏感信息流向未经验证的渠道。
        sensitive_category = self._detect_sensitive(text)
        if sensitive_category:
            logger.warning(
                "[ai-gateway-matrix] 检测到疑似敏感信息（类别: %s），"
                "强制路由到 trusted-pool（仅官方直营渠道，不含 Agnes AI/中转站）",
                sensitive_category,
            )
            return TRUSTED_POOL

        text_lower = text.lower()

        # 规则 1-3：关键词/正则/文件数快速判断
        quick_result = self._quick_escalation_check(text, text_lower)
        if quick_result is not None:
            return quick_result

        # 规则 4–5：超长 → 顶级；较长 → 强
        token_count = self._estimate_token_count(text)
        if token_count >= ELITE_TOKEN_THRESHOLD or len(text) >= ELITE_CHAR_THRESHOLD:
            logger.info(
                "[ai-gateway-matrix] 升级到 elite-model-pool（粗估 %d tokens / %d 字）",
                token_count, len(text),
            )
            return ELITE_POOL
        if token_count >= TOKEN_THRESHOLD or len(text) >= CHAR_THRESHOLD:
            logger.info(
                "[ai-gateway-matrix] 升级到 strong-model-pool（粗估 %d tokens / %d 字）",
                token_count, len(text),
            )
            return STRONG_POOL

        floor_pool = self._minimum_pool_for_text(text)

        # 规则 6：只有明确的简单短任务才走弱档；其它短任务至少走中档。
        if (
            token_count < FAST_TOKEN_THRESHOLD
            and len(text) < FAST_CHAR_THRESHOLD
            and self._is_trivial_task(text)
        ):
            logger.info(
                "[ai-gateway-matrix] 路由到 fast-pool（%d tokens / %d 字符，明确简单任务）",
                token_count, len(text)
            )
            return FAST_POOL

        if floor_pool is not None:
            return floor_pool

        # 默认：中档
        return FREE_POOL

    @staticmethod
    def _is_configured(channel: dict) -> bool:
        # 同步仪表盘写入的 .env，再判断（否则会出现「已填 Key 仍提示无渠道」）
        try:
            from gateway import env_sync
            env_sync.ensure_synced()
        except Exception:
            pass
        env_var = channel.get("env_var")
        if not env_var:
            return False
        value = os.environ.get(env_var, "").strip()
        try:
            from gateway.runtime_launcher import _is_placeholder_credential
            return not _is_placeholder_credential(value)
        except Exception:
            return bool(value) and not value.startswith("dummy-")

    async def _resolve_capability_target(self, pool: str, requirements: set[str]) -> str:
        if self._provider_registry is None:
            raise RuntimeError("供应商注册表不可用，拒绝在未知凭据状态下猜测路由")
        # 可用性优先（个人多免费 Key）：
        # 优先用目标档；该档没有任何「已配置 Key」时，立刻换其它档，
        # 而不是因为某一个免费模型倒闭/空 Key 就整次失败。
        # 同池内多个 deployment 的 401/404 由 LiteLLM weighted failover + fallbacks 换 peer。
        # 敏感请求仍只走 trusted，不在此扩展。
        fallback_pools = {
            FAST_POOL: (FAST_POOL, FREE_POOL, STRONG_POOL),
            FREE_POOL: (FREE_POOL, STRONG_POOL),
            # 强档是质量回退的终点，绝不能继续降到中/弱，也不反向升级顶级。
            STRONG_POOL: (STRONG_POOL,),
            # 顶级池的 peer 全部不可用后只降一次强档，到此为止。
            ELITE_POOL: (ELITE_POOL, STRONG_POOL),
        }.get(pool, (pool,))
        required = ", ".join(sorted(requirements - {"text"})) or "text"
        had_candidates = False
        for candidate_pool in fallback_pools:
            candidates = [
                channel
                for channel
                in self._provider_registry.candidates(candidate_pool, requirements)
                if self._is_configured(channel)
            ]
            if not candidates:
                continue
            had_candidates = True
            # 普通文本：交给 Router 在池内换 peer、再按 config fallbacks 跨池；
            # 但若该池所有渠道都在自定义长/短冷却中，就必须继续尝试下一档。
            # 旧逻辑只看“是否配置了 Key”，会把 mode-elite 永远送进一个已被
            # 全部冷却的 elite 池，最终直接 429/500，无法降到仍健康的强档。
            if requirements == {"text"}:
                available = [
                    channel for channel in candidates
                    if await quota_manager.cooldown_remaining(
                        str(channel["display_id"])
                    ) <= 0
                ]
                if not available:
                    logger.info(
                        "[ai-gateway-matrix] %s 已配置渠道均在运行期冷却，改试下一档",
                        candidate_pool,
                    )
                    continue
                if candidate_pool != pool:
                    logger.info(
                        "[ai-gateway-matrix] %s 无已配置渠道，改走 %s（可用性优先）",
                        pool,
                        candidate_pool,
                    )
                return candidate_pool
            selected = await quota_manager.choose_and_reserve(candidates)
            if selected is not None:
                return selected["direct_model_name"]

        if had_candidates:
            raise RuntimeError(
                f"支持 {required} 的已配置渠道当前额度均不可用（已尝试各档位）"
            )
        raise RuntimeError(
            f"没有已配置且支持 {required} 的渠道，请先在仪表盘填写至少一个有效 API Key"
        )

    async def _sensitive_target(
        self, data: dict, requirements: set[str]
    ) -> Optional[str]:
        """返回敏感请求的安全目标；未命中返回 None。所有聊天/补全入口共用。"""
        security_text = (
            self._provider_registry.security_text(data)
            if self._provider_registry is not None else self._extract_text(data)
        )
        sensitive_category = self._detect_sensitive(security_text)
        if not sensitive_category:
            return None
        logger.warning(
            "[ai-gateway-matrix] 检测到疑似敏感信息（类别: %s），"
            "强制使用符合数据政策的渠道",
            sensitive_category,
        )
        candidates = [
            channel
            for channel in self._provider_registry.sensitive_candidates(requirements)
            if self._is_configured(channel)
        ] if self._provider_registry is not None else []
        if not candidates:
            raise RuntimeError("敏感请求没有已配置且符合数据政策的可用渠道")
        if requirements == {"text"}:
            return TRUSTED_POOL
        selected = await quota_manager.choose_and_reserve(candidates)
        if selected is None:
            raise RuntimeError("敏感请求需要的能力没有符合数据政策的可用渠道")
        return selected["direct_model_name"]

    async def _pick_optimal_channel(
        self, requirements: set[str], required_pool: str
    ) -> Optional[str]:
        """检查是否存在仍然有效的"限时优先"渠道，有就返回它的直连 model_name。

        "有效"指：还没过期（Redis key 的 TTL 保证了这一点，list_optimal()
        只会返回没过期的），档位不低于任务所需档位，而且这一分钟还没打满
        RPM。多个标记同时存在时按最快过期的优先尝试，都不可用才回退到正常池。
        """
        try:
            flagged = await optimal_channels.list_optimal()
        except Exception as exc:
            logger.warning("[ai-gateway-matrix] 查询限时优先渠道列表失败: %s", exc)
            return None

        for item in flagged:
            display_id = item.get("display_id")
            channel = self._channel_registry.get(display_id)
            if channel is None:
                continue  # 标记的渠道不在当前 config.yaml 里（比如渠道被删了），跳过
            if not self._is_configured(channel):
                continue
            channel_pool = str(channel.get("pool") or "")
            if POOL_RANK.get(channel_pool, -1) < POOL_RANK.get(required_pool, 0):
                logger.info(
                    "[ai-gateway-matrix] 限时优先渠道 %s 属于 %s，低于任务所需 %s，跳过",
                    display_id,
                    channel_pool,
                    required_pool,
                )
                continue
            capabilities = channel.get("capabilities") or {}
            if any(not capabilities.get(requirement, False) for requirement in requirements):
                logger.info("[ai-gateway-matrix] 限时优先渠道 %s 不支持本次请求能力，跳过", display_id)
                continue
            if not await quota_manager.reserve_channel(channel):
                logger.info("[ai-gateway-matrix] 限时优先渠道 %s 共享凭据额度已满，跳过", display_id)
                continue

            logger.info(
                "[ai-gateway-matrix] 命中限时优先渠道 %s，能力与额度检查通过",
                display_id,
            )
            return channel["direct_model_name"]

        return None

    async def _resolve_pool_with_optimal(
        self, pool: str, requirements: set[str]
    ) -> str:
        """优先使用能够覆盖任务档位的标记渠道，否则回到正常池。"""
        optimal_target = await self._pick_optimal_channel(requirements, pool)
        if optimal_target is not None:
            self._stats["routed_to_optimal"] += 1
            return optimal_target
        return await self._resolve_capability_target(pool, requirements)

    async def decide_pool_with_classifier(
        self,
        data: dict,
        *,
        preserve_pool: bool = False,
    ) -> str:
        """智能模式主路径：强模型判强度 → 再选池作答。

        优先级：
          1. 敏感内容 → trusted-pool（隐私优先，不做分诊）
          2. 规则/分诊先确定任务所需档位
          3. 标记渠道档位不低于所需档位时优先使用
          4. 标记渠道不可用时回到正常池
          5. 分诊失败 → decide_pool() 启发式兜底
        """
        text = self._extract_text(data)
        resolve_target = (
            self._resolve_capability_target
            if preserve_pool else self._resolve_pool_with_optimal
        )
        requirements = (
            self._provider_registry.request_requirements(data)
            if self._provider_registry is not None else {"text"}
        )
        sensitive_target = await self._sensitive_target(data, requirements)
        if sensitive_target is not None:
            return sensitive_target

        if not text:
            return await resolve_target(FAST_POOL, requirements)

        text_lower = text.lower()
        quick_result = self._quick_escalation_check(text, text_lower)
        if quick_result is not None:
            return await resolve_target(quick_result, requirements)

        token_count = self._estimate_token_count(text)
        # 长度是硬约束，必须在只看前 2000 字符的外部分类器之前生效，
        # 避免长请求被截断样本误判成弱档。
        if token_count >= ELITE_TOKEN_THRESHOLD or len(text) >= ELITE_CHAR_THRESHOLD:
            return await resolve_target(ELITE_POOL, requirements)
        if token_count >= TOKEN_THRESHOLD or len(text) >= CHAR_THRESHOLD:
            return await resolve_target(STRONG_POOL, requirements)
        floor_pool = self._minimum_pool_for_text(text)
        if (
            token_count < FAST_TOKEN_THRESHOLD
            and len(text) < FAST_CHAR_THRESHOLD
            and self._is_trivial_task(text)
        ):
            self._stats["classifier_skipped_trivial"] += 1
            logger.info(
                "[ai-gateway-matrix] 明确简单短任务（%d tokens / %d 字符），跳过分类器直接走 fast-pool",
                token_count, len(text),
            )
            return await resolve_target(FAST_POOL, requirements)

        pool = await llm_classifier.classify_task(text)
        if pool is not None:
            self._stats["classifier_used"] += 1
            pool = self._higher_pool(pool, floor_pool)
            return await resolve_target(pool, requirements)

        # 分类器不可用/失败 → 回退到纯规则启发式（decide_pool 内部会重新走一遍
        # 敏感检测/关键词/正则/文件数/token 数判断；这里不重复造轮子）
        self._stats["classifier_fallback_to_heuristic"] += 1
        fallback_pool = self.decide_pool(data)
        return await resolve_target(fallback_pool, requirements)

    # ──────────────────────────────────────────────────────────────
    #  LiteLLM Hook 接口实现
    # ──────────────────────────────────────────────────────────────

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: dict,
        call_type: str,
    ) -> Optional[dict]:
        """请求发出前的拦截点。

        v2 修复：
          · 签名增加 cache 参数（LiteLLM 1.90.x 要求）
          · call_type 检查覆盖所有补全类（acompletion / completion /
            atext_completion / text_completion），原版只检查同步变体导致
            hook 在异步代理路径下完全不生效。
        """
        self._stats["total_requests"] += 1

        # 仪表盘改 .env 后：热同步环境变量 + 重建 Router 模型列表
        try:
            from gateway import env_sync
            env_sync.ensure_synced()
        except Exception:
            pass
        self._ensure_channel_registry_fresh()

        # v2 CRITICAL FIX: 覆盖所有补全类 call_type
        # LiteLLM Proxy 对 /v1/chat/completions 传入的是 "acompletion"（异步），
        # 对 /v1/completions 传入的是 "atext_completion"（异步）。
        # 原版只检查 ("completion", "text_completion")，永远不匹配，hook 直接 return。
        if call_type not in (
            "completion",
            "acompletion",
            "text_completion",
            "atext_completion",
        ):
            self._stats["non_completion_skipped"] += 1
            return data

        # ── 双模式：strict 强制非流式质检换家；agent-stream 保留客户端流式 ──
        # 模式来源：metadata.privateapi_mode / litellm_metadata / 默认 agent-stream
        request_mode = self._resolve_quality_stream_mode(data)
        data["_gwmatrix_request_mode"] = request_mode
        client_wanted_stream = bool(data.get("stream"))
        if request_mode == "strict":
            # 完整正文 → 质检 → 不合格换 peer；用户只见最终通过结果
            if client_wanted_stream or data.get("stream_options") is not None:
                self._stats["stream_forced_off"] = int(self._stats.get("stream_forced_off") or 0) + 1
                logger.info(
                    "[ai-gateway-matrix] strict 模式强制 stream=false（原 stream=%s）",
                    data.get("stream"),
                )
            data["stream"] = False
            data.pop("stream_options", None)
            data["_gwmatrix_quality"] = True
            data["_gwmatrix_quality_retry"] = True
        else:
            # agent-stream：Cline/Roo 需要真实 SSE；首 token 后禁止无痕换模型拼接
            data["_gwmatrix_quality"] = True
            data["_gwmatrix_quality_retry"] = not client_wanted_stream
            if client_wanted_stream:
                self._stats["agent_stream_passthrough"] = (
                    int(self._stats.get("agent_stream_passthrough") or 0) + 1
                )
                logger.info("[ai-gateway-matrix] agent-stream 保留 stream=true")
            else:
                # 非流式 agent 请求仍可质检换家
                data["stream"] = False
                data.pop("stream_options", None)

        requested_model = str(data.get("model") or "").strip()

        response_format = data.get("response_format")
        soft_json_object = (
            isinstance(response_format, dict)
            and response_format.get("type") == "json_object"
        )
        if soft_json_object:
            # 标题、分类等短 JSON 任务不应让单个上游占满全局 120 秒。
            # 该超时随请求进入 Router；失败后仍可在模型组内换 peer/fallback。
            current_timeout = data.get("timeout")
            if not isinstance(current_timeout, (int, float)) or current_timeout > 30:
                data["timeout"] = 30

        # ── 模式解析：智能 / 弱 / 中 / 强 / 顶级 ─────────────────
        # 智能 → 按提问选档；固定档 → 只使用该档（敏感仍强制 trusted）
        mode = self._resolve_client_mode(requested_model)
        if mode is None:
            # 直连 deployment：仍强制非流式 + 质检，但不改写 model
            data["_gwmatrix_quality"] = True
            return data

        requirements = (
            self._provider_registry.request_requirements(data)
            if self._provider_registry is not None else {"text"}
        )

        # 所有模式：敏感内容优先
        sensitive_target = await self._sensitive_target(data, requirements)
        if sensitive_target is not None:
            data["model"] = sensitive_target
            self._stats["routed_to_trusted_sensitive"] += 1
            logger.info(
                "[ai-gateway-matrix] 路由决策: %s → %s (敏感, call_type=%s)",
                requested_model, sensitive_target, call_type,
            )
            return data

        if mode == "intelligent":
            target_pool = await self.decide_pool_with_classifier(
                data,
                preserve_pool=soft_json_object,
            )
        else:
            # 固定档位：仍做能力/配置解析与可用性跨档兜底
            forced = {
                "weak": FAST_POOL,
                "mid": FREE_POOL,
                "strong": STRONG_POOL,
                "elite": ELITE_POOL,
            }[mode]
            if soft_json_object:
                target_pool = await self._resolve_capability_target(forced, requirements)
            else:
                target_pool = await self._resolve_pool_with_optimal(forced, requirements)

        data["model"] = target_pool
        data["_gwmatrix_quality"] = True
        data["_gwmatrix_mode"] = mode
        data["_gwmatrix_pool"] = str(target_pool)

        if target_pool == TRUSTED_POOL:
            self._stats["routed_to_trusted_sensitive"] += 1
        elif str(target_pool).startswith("direct-"):
            self._stats["routed_to_optimal"] = int(self._stats.get("routed_to_optimal") or 0) + 1
        elif target_pool == ELITE_POOL:
            self._stats["escalated_to_strong"] += 1  # 复用计数：顶级也算升档
        elif target_pool == STRONG_POOL:
            self._stats["escalated_to_strong"] += 1
        elif target_pool == FAST_POOL:
            self._stats["routed_to_fast"] += 1
        else:
            self._stats["routed_to_free"] += 1

        logger.info(
            "[ai-gateway-matrix] 路由决策: %s (mode=%s) → %s (call_type=%s, stream=false, quality=on)",
            requested_model, mode, target_pool, call_type,
        )

        return data

    @staticmethod
    def _deployment_params(data: dict) -> dict:
        nested = data.get("litellm_params")
        return nested if isinstance(nested, dict) else data

    def _extract_display_id_from_request(self, data: dict) -> Optional[str]:
        params = self._deployment_params(data)
        requested_model = str(params.get("model") or data.get("model") or "")
        direct_matches = [
            channel for channel in self._channel_registry.values()
            if channel.get("direct_model_name") == requested_model
        ]
        if len(direct_matches) == 1:
            return direct_matches[0]["display_id"]
        return self._extract_display_id({"litellm_params": params})

    async def async_pre_call_deployment_hook(
        self, kwargs: dict, call_type: Any
    ) -> Optional[dict]:
        """deployment 选定后应用运行期健康熔断和模型专属参数。"""
        self._ensure_channel_registry_fresh()
        params = self._deployment_params(kwargs)
        model = str(params.get("model") or kwargs.get("model") or "").lower()
        display_id = self._extract_display_id_from_request(kwargs)
        if display_id:
            remaining = await quota_manager.cooldown_remaining(display_id)
            if remaining > 0:
                logger.info(
                    "[ai-gateway-matrix] 跳过运行期冷却渠道 %s（剩余 %d 秒）",
                    display_id,
                    remaining,
                )
                provider_name = model.split("/", 1)[0] if "/" in model else "custom"
                # RuntimeError 会被 LiteLLM Proxy 映射成 HTTP 500，并可能中止
                # fallback；RateLimitError 才表示“当前 deployment 暂不可用，
                # 请换 peer”。最终耗尽时也应返回 429，而不是泄漏内部 500。
                raise litellm.RateLimitError(
                    message="channel_cooldown_active",
                    llm_provider=provider_name,
                    model=model or "unknown",
                )

        if "gemini-3.5-flash" in model:
            # Gemini 3.5 Flash 默认会思考。强档任务用最小思考，且给最终答案
            # 留出足够预算；温度低于 1 会触发官方/LiteLLM 的退化警告。
            params["reasoning_effort"] = "minimal"
            # 使用模型默认温度；Gemini 3+ 已把显式采样参数标为弃用。
            params.pop("temperature", None)
            max_tokens = params.get("max_tokens")
            if not isinstance(max_tokens, (int, float)) or max_tokens < 512:
                params["max_tokens"] = 512
        return kwargs

    @staticmethod
    def _response_text(response: Any) -> str:
        try:
            choices = getattr(response, "choices", None) or []
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", None)
            if isinstance(content, str):
                return content
        except Exception:
            pass
        return ""

    @staticmethod
    def _response_has_tool_call(response: Any) -> bool:
        """空 content 但带工具调用是合法响应，不能误判为空答案。"""
        try:
            choices = getattr(response, "choices", None) or []
            message = getattr(choices[0], "message", None)
            return bool(
                getattr(message, "tool_calls", None)
                or getattr(message, "function_call", None)
            )
        except Exception:
            return False

    @staticmethod
    def _last_user_text(data: dict) -> str:
        params = ComplexityRouterHook._deployment_params(data)
        messages = params.get("messages") or data.get("messages") or []
        for message in reversed(messages):
            if isinstance(message, dict) and message.get("role") == "user":
                content = message.get("content")
                if isinstance(content, str):
                    return content
        return ""

    @staticmethod
    def _quality_failure_reason(prompt: str, output: str) -> Optional[str]:
        """识别确定性较高的坏输出：空、乱码、模板泄漏、上游报错、复读。

        适用于智能 / 弱 / 中 / 强 / 顶级所有入口；不评价观点对错。
        """
        stripped = output.strip()
        if not stripped:
            return "empty_output"
        lowered = stripped.lower()
        role_markers = (
            "<|im_start|>", "<|im_end|>", "<|assistant|>", "<|user|>",
            "\nuser\n", "\nassistant\n", "[INST]", "[/INST]",
        )
        if any(marker in lowered or marker in stripped for marker in role_markers):
            return "chat_template_echo"
        # 替换符 / 控制字符 / 明显坏编码
        if "�" in stripped:
            return "mojibake"
        if any(ord(ch) < 32 and ch not in "\n\r\t" for ch in stripped):
            return "mojibake"
        # 高比例私用区/不可见字符（部分小模型乱码）
        if len(stripped) >= 20:
            weird = sum(
                1 for ch in stripped
                if (0xE000 <= ord(ch) <= 0xF8FF) or (0xFFF0 <= ord(ch) <= 0xFFFF)
            )
            if weird / len(stripped) >= 0.08:
                return "mojibake"
        # HTML 错误页
        if re.search(r"(?i)<!doctype\s+html|<html[\s>]|cloudflare|bad gateway|502 bad", stripped[:800]):
            return "html_error_page"
        # JSON 错误体 / 上游把 4xx 塞进 200
        if len(stripped) < 1200 and _ERROR_JSON_HINT.search(stripped):
            if _UPSTREAM_ERROR_BODY.search(stripped) or re.search(
                r"(?i)\"(?:code|status)\"\s*:\s*(?:4\d\d|5\d\d)", stripped
            ):
                return "upstream_error_json"
        if len(stripped) < 600 and _UPSTREAM_ERROR_BODY.search(stripped):
            # 避免把正常讲解「rate limit 是什么」的长文误杀：短且像报错
            if len(stripped) < 280 or stripped.count("\n") <= 3:
                return "upstream_error_text"
        # 字符熵极低的复读垃圾
        if len(stripped) >= 40:
            unique = len(set(stripped))
            if unique <= 4:
                return "low_entropy"
        # 只校验题面可无歧义算出的简单加减应用题，不对一般语义回答评分。
        arithmetic = re.search(
            r"有\s*(\d+)\s*个?.*?送出\s*(\d+)\s*个?.*?(?:又|再)?买(?:了)?\s*(\d+)\s*个?",
            prompt,
            re.DOTALL,
        )
        if arithmetic:
            expected = int(arithmetic.group(1)) - int(arithmetic.group(2)) + int(arithmetic.group(3))
            if not re.search(rf"(?<!\d){expected}(?!\d)", stripped):
                return "arithmetic_mismatch"
        # 纯数字四则表达式同样可以确定性校验，但只允许“短小、明确、以算式
        # 为主体”的请求进入。小说评分/分类 prompt 常含“每项 0-10 分”和
        # “输出结果”；旧逻辑会把其中的 0-10 当成算术题，期待答案 -10，
        # 从而把正常 JSON 响应误判为 arithmetic_mismatch。
        arithmetic_request = (
            len(prompt) <= 240
            and not re.search(r"(?:评分|分数|满分|权重|区间|范围|等级|每项)", prompt)
            and re.search(r"(?:等于多少|是多少|计算|算一下|求值|what\s+is)", prompt, re.I)
        )
        if arithmetic_request:
            expression = re.search(
                r"(?<![\w.])(\d+(?:\.\d+)?(?:\s*(?:\*\*|//|[+\-*/%])\s*\d+(?:\.\d+)?)+)",
                prompt,
            )
            # 算式必须位于问题开头附近；正文/规则中偶然出现的范围或公式
            # 不能拿来验证整个回答。
            if expression and expression.start() <= 24:
                try:
                    tree = ast.parse(expression.group(1), mode="eval")

                    def calculate(node: ast.AST) -> float:
                        if isinstance(node, ast.Expression):
                            return calculate(node.body)
                        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                            return float(node.value)
                        if isinstance(node, ast.BinOp):
                            left, right = calculate(node.left), calculate(node.right)
                            if isinstance(node.op, ast.Add):
                                return left + right
                            if isinstance(node.op, ast.Sub):
                                return left - right
                            if isinstance(node.op, ast.Mult):
                                return left * right
                            if isinstance(node.op, ast.Div):
                                return left / right
                            if isinstance(node.op, ast.FloorDiv):
                                return left // right
                            if isinstance(node.op, ast.Mod):
                                return left % right
                            if isinstance(node.op, ast.Pow) and abs(right) <= 10:
                                return left ** right
                        raise ValueError("unsupported arithmetic expression")

                    value = calculate(tree)
                    expected_text = str(int(value)) if value.is_integer() else f"{value:.10g}"
                    if not re.search(rf"(?<![\d.]){re.escape(expected_text)}(?![\d.])", stripped):
                        return "arithmetic_mismatch"
                except (ArithmeticError, SyntaxError, ValueError, OverflowError):
                    pass
        normalized_prompt = re.sub(r"\s+", "", prompt).lower()
        normalized_output = re.sub(r"\s+", "", stripped).lower()
        if len(normalized_prompt) >= 24 and normalized_prompt[:48] in normalized_output:
            return "prompt_echo"
        lines = [line.strip() for line in stripped.splitlines() if len(line.strip()) >= 8]
        if lines and len(lines) >= 3 and max(lines.count(line) for line in set(lines)) >= 3:
            return "repeated_lines"
        return None

    async def _evaluate_response_quality(
        self, request_data: dict, response: Any
    ) -> Optional[str]:
        """统一质检：智能/弱/中/强/顶级 共用。返回失败原因或 None。"""
        if self._response_has_tool_call(response):
            # 有 tool_calls 时 content 为空是合法的
            output = self._response_text(response)
            if not output.strip():
                return None
        output = self._response_text(response)
        prompt = self._last_user_text(request_data)
        if not output.strip():
            return "empty_output"
        reason = self._quality_failure_reason(prompt, output)
        if reason is not None:
            return reason
        # 本地硬规则未命中：hybrid/dedicated 再对可疑输出用智脑短检
        mode = answer_verifier.verify_mode()
        if mode in {"off", "local"}:
            # local 模式：仍把 soft_suspect 里高置信的当硬失败
            suspect = answer_verifier.soft_suspect(prompt, output)
            if suspect in {"upstream_error_text", "low_entropy", "repeat_chunks"}:
                return suspect
            return None
        force_sample = False
        try:
            import random
            rate = float(os.environ.get("ANSWER_VERIFY_SAMPLE_RATE") or "0")
            display_id_probe = self._extract_display_id_from_request(request_data)
            channel = (
                self._channel_registry.get(display_id_probe or "")
                if display_id_probe else None
            )
            trust = str((channel or {}).get("trust") or "")
            # 观察期/中转站 + 所有固定档/智能档统一更积极抽检
            if trust in {"observation", "relay"} and rate <= 0:
                rate = 0.12
            force_sample = rate > 0 and random.random() < min(1.0, rate)
        except Exception:
            force_sample = False
        llm_reason = await answer_verifier.should_reject_with_llm(
            prompt, output, force_sample=force_sample,
        )
        if llm_reason:
            return llm_reason if llm_reason in UNIVERSAL_QUALITY_FAILURES else "llm_reject"
        return None

    async def _reject_bad_response(
        self, request_data: dict, reason: str, *, count_usage: bool = False
    ) -> None:
        """标记坏输出并抛错，让 Router 换同档 peer（所有模式通用）。"""
        if count_usage:
            # 流式结束后才发现坏内容：已记过用量则不再记
            pass
        display_id = self._extract_display_id_from_request(request_data)
        # arithmetic / 单次过短：只重试本次，不长冷却
        if display_id and reason not in {
            "arithmetic_mismatch", "llm_reject", "too_short",
        }:
            await quota_manager.mark_failure(display_id, "quality_error")
        if not isinstance(getattr(self, "_stats", None), dict):
            self._stats = {}
        self._stats["errors"] = int(self._stats.get("errors") or 0) + 1
        logger.warning(
            "[ai-gateway-matrix] 输出质检失败（渠道=%s，原因=%s），切换同档 peer",
            display_id or "unknown",
            reason,
        )
        raise RuntimeError(f"response_quality_error:{reason}")

    async def async_post_call_success_deployment_hook(
        self, request_data: dict, response: Any, call_type: Any
    ) -> Optional[Any]:
        """成功后质检：strict/非流式可换 peer；agent-stream 流式仅标记不拼接重试。

        智能 / mode-weak / mid / strong / elite / 直连 全部走此路径。
        """
        params = self._deployment_params(request_data)
        is_stream = bool(params.get("stream") or request_data.get("stream"))
        allow_retry = bool(request_data.get("_gwmatrix_quality_retry", not is_stream))

        # LiteLLM Proxy 路径上 deployment 成功钩子稳定执行，在此落账
        await self._record_success_usage(request_data, response)
        reason = await self._evaluate_response_quality(request_data, response)
        if reason is None:
            return response
        if allow_retry and not is_stream:
            await self._reject_bad_response(request_data, reason)
            return response  # pragma: no cover
        # agent-stream 已向客户端推流：只冷却/记质量问题，不换模型拼接
        if not isinstance(getattr(self, "_stats", None), dict):
            self._stats = {}
        self._stats["stream_quality_flagged"] = (
            int(self._stats.get("stream_quality_flagged") or 0) + 1
        )
        display_id = self._extract_display_id_from_request(request_data)
        if display_id and reason not in {
            "arithmetic_mismatch",
            "llm_reject",
            "too_short",
        }:
            try:
                await quota_manager.mark_failure(display_id, "quality_error")
            except Exception:
                pass
        logger.warning(
            "[ai-gateway-matrix] agent-stream 质检失败仅标记（渠道=%s，原因=%s），不拼接重试",
            display_id or "unknown",
            reason,
        )
        return response

    @staticmethod
    def _resolve_quality_stream_mode(data: dict) -> str:
        """strict | agent-stream — from metadata or default agent-stream."""
        meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        litellm_meta = (
            data.get("litellm_metadata")
            if isinstance(data.get("litellm_metadata"), dict)
            else {}
        )
        raw = (
            (meta or {}).get("privateapi_mode")
            or (litellm_meta or {}).get("privateapi_mode")
            or data.get("_gwmatrix_request_mode")
            or os.environ.get("DEFAULT_REQUEST_MODE")
            or "agent-stream"
        )
        mode = str(raw).strip().lower()
        if mode in {"strict", "agent-stream"}:
            return mode
        return "agent-stream"

    @staticmethod
    def _resolve_client_mode(requested_model: str) -> Optional[str]:
        """返回 intelligent|weak|mid|strong|elite；非入口模型返回 None。"""
        name = (requested_model or "").strip()
        if name in MODE_INTELLIGENT:
            return "intelligent"
        if name in MODE_WEAK:
            return "weak"
        if name in MODE_MID:
            return "mid"
        if name in MODE_STRONG:
            return "strong"
        if name in MODE_ELITE:
            return "elite"
        return None

    # ──────────────────────────────────────────────────────────────
    #  用量追踪（v5 新增，配合浏览器仪表盘）
    # ──────────────────────────────────────────────────────────────

    def _extract_channel_id(self, kwargs: dict) -> Optional[str]:
        """从 LiteLLM 的 logging kwargs 里提取这次请求实际用的是哪个 deployment。

        kwargs["litellm_params"] 是 Router 选中某个具体 deployment 之后、
        真正拿去发请求的参数（不是 "auto-route"/"free-pool" 这种池子名），
        所以用它的 model + api_base 拼出的 channel_id 跟 dashboard/backend.py
        解析 config.yaml 时生成的 id 是同一套，两边才能对得上。
        """
        litellm_params = self._deployment_params(kwargs)
        model = litellm_params.get("model")
        if not model:
            return None
        api_base = litellm_params.get("api_base")
        api_key = litellm_params.get("api_key")
        # 尽量挂上 env，走凭据级共用账本
        env_var = None
        display_id = self._extract_display_id(kwargs)
        ch = self._channel_registry.get(display_id or "") if display_id else None
        if ch:
            env_var = ch.get("env_var")
        return usage_tracker.make_usage_key(
            str(model), api_base, api_key, env_var=env_var,
        )

    def _extract_display_id(self, kwargs: dict) -> Optional[str]:
        """将 LiteLLM 已选中的 deployment 反查回不含密钥的稳定标识。"""
        # LiteLLM 的常规日志回调使用嵌套 litellm_params；部分失败路径会把
        # deployment 参数直接放在顶层。两种形态都要支持，否则 429 无法写入冷却。
        params = self._deployment_params(kwargs)
        model = params.get("model")
        api_base = params.get("api_base")
        if not model:
            return None
        direct_matches = [
            channel for channel in self._channel_registry.values()
            if channel.get("direct_model_name") == str(model)
        ]
        if len(direct_matches) == 1:
            return direct_matches[0]["display_id"]
        matches = [
            channel for channel in self._channel_registry.values()
            if channel.get("model") == model and channel.get("api_base") == api_base
        ]
        if len(matches) == 1:
            return matches[0]["display_id"]
        # 原生 provider 可能在调用期补全真实 api_base，而注册表里是 None。
        # 模型名唯一时仍可安全反查，保证失败能写入对应渠道的运行期熔断。
        model_text = str(model)
        model_matches = []
        for channel in self._channel_registry.values():
            registered = str(channel.get("model") or "")
            if (
                registered == model_text
                or registered.endswith("/" + model_text)
                or model_text.endswith("/" + registered)
            ):
                model_matches.append(channel)
        if not matches and len(model_matches) == 1:
            return model_matches[0]["display_id"]
        resolved_key = params.get("api_key")
        if resolved_key is not None:
            resolved_key = str(resolved_key)
            for channel in matches:
                env_value = os.environ.get(channel.get("env_var") or "")
                if env_value and env_value == resolved_key:
                    return channel["display_id"]
        return None

    def _failure_display_ids(
        self,
        request_data: dict,
        error: Any,
        error_class: str,
    ) -> list[str]:
        """从 Router 请求和实际异常中反查应该冷却的渠道。

        请求级失败钩子里的 ``request_data[model]`` 经常仍是
        ``elite-model-pool``，但 LiteLLM 异常对象保留了实际 deployment 的
        ``model`` / ``llm_provider``。额度和鉴权错误通常属于整个凭据，因此
        命中实际模型后扩展到使用同一 env_var 的所有模型。
        """
        found: set[str] = set()
        request_match = self._extract_display_id_from_request(request_data)
        if request_match:
            found.add(request_match)

        providers: set[str] = set()
        current = error
        seen: set[int] = set()
        for _ in range(4):
            if current is None or id(current) in seen:
                break
            seen.add(id(current))
            actual_model = str(getattr(current, "model", "") or "").strip()
            actual_provider = str(
                getattr(current, "llm_provider", "")
                or getattr(current, "custom_llm_provider", "")
                or ""
            ).strip().lower()
            if actual_provider:
                providers.add(actual_provider)
            if actual_model and actual_model not in ALL_CAPABILITY_POOLS:
                matched = self._extract_display_id({"model": actual_model})
                if matched:
                    found.add(matched)
            current = getattr(current, "__cause__", None) or getattr(
                current, "__context__", None
            )

        credential_scoped = error_class in {
            "auth_error", "quota_zero", "quota_error",
        }
        if credential_scoped and found:
            env_vars = {
                str(self._channel_registry[item].get("env_var") or "")
                for item in found
                if item in self._channel_registry
            }
            found.update(
                str(channel["display_id"])
                for channel in self._channel_registry.values()
                if str(channel.get("env_var") or "") in env_vars
                and str(channel.get("env_var") or "")
            )
        elif credential_scoped and providers:
            # 只有异常没有实际 model 时才按明确的原生 provider 兜底；openai
            # 是大量兼容端点的协议名，不能据此冻结所有 OpenAI-compatible 渠道。
            native_providers = providers - {"openai", "custom"}
            found.update(
                str(channel["display_id"])
                for channel in self._channel_registry.values()
                if str(channel.get("model") or "").split("/", 1)[0].lower()
                in native_providers
            )
        return sorted(found)

    @staticmethod
    def _response_usage_value(usage: Any, name: str) -> int:
        if isinstance(usage, dict):
            value = usage.get(name, 0)
        else:
            value = getattr(usage, name, 0) if usage is not None else 0
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def _resolved_usage_channel_id(self, kwargs: dict) -> Optional[str]:
        """优先从注册表构造与 Dashboard 一致的 usage key（同 Key 共用）。"""
        display_id = self._extract_display_id_from_request(kwargs)
        channel = self._channel_registry.get(display_id or "")
        if channel is not None:
            env_var = str(channel.get("env_var") or "") or None
            env_value = os.environ.get(env_var or "", "") if env_var else ""
            return usage_tracker.make_usage_key(
                str(channel.get("model") or ""),
                channel.get("api_base"),
                env_value or None,
                env_var=env_var,
            )
        return self._extract_channel_id(kwargs)

    async def _record_success_usage(
        self,
        kwargs: dict,
        response_obj: Any,
        start_time: Any = None,
        end_time: Any = None,
    ) -> None:
        """从实际 deployment 响应记账；可由 deployment/logging 两种钩子安全复用。"""
        channel_id = self._resolved_usage_channel_id(kwargs)
        if not channel_id:
            logger.warning("[ai-gateway-matrix] 成功请求无法解析实际渠道，跳过 token 记账")
            return
        display_id = self._extract_display_id_from_request(kwargs)

        usage = getattr(response_obj, "usage", None)
        if usage is None and isinstance(response_obj, dict):
            usage = response_obj.get("usage")
        prompt_tokens = self._response_usage_value(usage, "prompt_tokens")
        completion_tokens = self._response_usage_value(usage, "completion_tokens")
        if prompt_tokens + completion_tokens <= 0:
            text_len = len(self._extract_text(kwargs))
            prompt_tokens = max(1, text_len // 4)
            completion_tokens = 1

        params = self._deployment_params(kwargs)
        cost: Optional[float] = None
        cost_source = "unknown"
        try:
            cost, cost_source = pricing.compute_cost(
                str(params.get("model") or ""),
                response_obj,
                prompt_tokens,
                completion_tokens,
                api_base=params.get("api_base"),
            )
        except Exception as exc:
            logger.debug("[ai-gateway-matrix] 花费计算失败（token 仍会记账）: %s", exc)

        latency_ms: Optional[float] = None
        try:
            if start_time is not None and end_time is not None:
                delta = end_time - start_time
                latency_ms = (
                    float(delta.total_seconds() * 1000)
                    if hasattr(delta, "total_seconds") else None
                )
        except Exception:
            pass

        response_id = getattr(response_obj, "id", None)
        if response_id is None and isinstance(response_obj, dict):
            response_id = response_obj.get("id")
        event_id = response_id or kwargs.get("litellm_call_id")
        await usage_tracker.record_call(
            channel_id,
            success=True,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=cost,
            cost_source=cost_source,
            latency_ms=latency_ms,
            event_id=str(event_id) if event_id else None,
        )
        if display_id:
            await quota_manager.mark_success(display_id)

    async def async_log_success_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
        """请求成功后记一笔用量——只做统计，任何异常都不应该往外抛。

        v7 新增：顺手记一笔 token 消耗和预计花费。花费计算（先查 litellm
        内置价格库，查不到再估算，都查不到就是 None）在 gateway/pricing.py 里，
        这里只负责把 usage 里的 token 数掏出来、调一下 pricing、
        再交给 usage_tracker 存起来——任何一步失败都不应该影响真实请求
        已经成功返回这件事，所以整段包在 try/except 里。
        """
        try:
            await self._record_success_usage(
                kwargs, response_obj, start_time=start_time, end_time=end_time,
            )
        except Exception as exc:
            logger.warning("[ai-gateway-matrix] 成功请求 token 记账失败: %s", type(exc).__name__)
        # 流式请求：deployment 钩子时常还没有正文。流结束后在此做一次质检，
        # 坏输出至少冷却渠道（响应可能已部分到达客户端，无法再换 peer）。
        try:
            is_stream = bool(
                (kwargs.get("litellm_params") or {}).get("stream")
                or kwargs.get("stream")
            )
            if not is_stream:
                return
            text = self._response_text(response_obj)
            if not text.strip():
                return
            reason = await self._evaluate_response_quality(kwargs, response_obj)
            if reason is None:
                return
            display_id = self._extract_display_id_from_request(kwargs)
            if display_id and reason not in {
                "arithmetic_mismatch", "llm_reject", "too_short",
            }:
                await quota_manager.mark_failure(display_id, "quality_error")
            logger.warning(
                "[ai-gateway-matrix] 流式结束后质检失败（渠道=%s，原因=%s），已冷却",
                display_id or "unknown",
                reason,
            )
        except Exception as exc:
            logger.debug("[ai-gateway-matrix] 流式质检跳过: %s", type(exc).__name__)

    async def async_log_failure_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
        """请求失败也记一笔（失败也占用了渠道的 RPM 配额，仪表盘应该看得到）。

        失败的请求通常没有 usage 数据（模型压根没返回结果），不统计
        token/花费，只统计"这次调用占用了一次配额"。
        """
        channel_id = self._extract_channel_id(kwargs)
        error = kwargs.get("exception") or response_obj
        error_class = self._classify_error(str(error or ""))
        latency_ms: Optional[float] = None
        try:
            delta = end_time - start_time
            latency_ms = float(delta.total_seconds() * 1000) if hasattr(delta, "total_seconds") else None
        except Exception:
            pass
        if channel_id:
            await usage_tracker.record_call(
                channel_id, success=False, latency_ms=latency_ms, error_class=error_class
            )
        # 模型改名/不存在：短冷却即可，留给 autofix；勿按 auth 长冷冻。
        fail_class = "rate_limit" if error_class in {"unknown"} and (
            "model" in str(error or "").lower()
        ) else error_class
        try:
            from gateway.model_autofix import is_model_name_error
            if is_model_name_error(str(error or "")):
                fail_class = "rate_limit"
        except Exception:
            pass
        # 自己抛出的冷却信号和 Router 全部耗尽是派生状态，不是一次新的
        # deployment 失败；重复写回会让短冷却不断自续期。
        if fail_class not in {"cooldown_active", "router_exhausted"}:
            for display_id in self._failure_display_ids(kwargs, error, fail_class):
                await quota_manager.mark_failure(display_id, fail_class)

        # 免费模型改名自愈：异步尝试 /models + 强模型裁决 + 写 config
        try:
            from gateway.model_autofix import is_model_name_error, maybe_autofix_from_failure
            if is_model_name_error(str(error or "")):
                asyncio.create_task(maybe_autofix_from_failure(kwargs))
        except Exception as exc:
            logger.debug("[ai-gateway-matrix] model autofix 调度失败: %s", exc)

    # ──────────────────────────────────────────────────────────────
    #  失败分类 & 日志
    # ──────────────────────────────────────────────────────────────

    def _classify_error(self, error_text: str) -> str:
        """把上游错误文本粗略分类，返回类别字符串。

        返回值：
          · "router_exhausted"  所有渠道同时不可用
          · "auth_error"        鉴权失败（key 失效/填错）
          · "quota_zero"        明确额度为 0（长熔断）
          · "quota_error"       额度/计费类错误（配额耗尽）
          · "quality_error"     模板泄漏/乱码等确定性坏输出
          · "rate_limit"        临时限流（429）
          · "timeout"           超时
          · "unknown"           其他
        """
        error_lower = error_text.lower()

        if "no deployments available" in error_lower or "all deployments" in error_lower:
            return "router_exhausted"
        if "channel_cooldown_active" in error_lower:
            return "cooldown_active"
        if "response_quality_error" in error_lower:
            return "quality_error"
        if "401" in error_lower or "unauthorized" in error_lower or "invalid api key" in error_lower:
            return "auth_error"
        # 明确的额度为 0 必须先于通用 429；否则会被误判成 8 秒临时限流。
        quota_zero_markers = (
            "limit: 0",
            'limit\": 0',
            "limit=0",
            "quota limit 0",
            "quota_limit=0",
        )
        if any(marker in error_lower for marker in quota_zero_markers):
            return "quota_zero"
        quota_markers = (
            "insufficient_quota",
            "exceeded your current quota",
            "quota exceeded",
            "resource_exhausted",
            "payment required",
            "billing",
            "402",
        )
        if any(marker in error_lower for marker in quota_markers):
            return "quota_error"
        # 真·额度耗尽（日/月额度用完）vs 临时拥挤：中文「访问量过大」是后者
        busy_markers = (
            "访问量过大",
            "稍后再试",
            "请您稍后再试",
            "系统繁忙",
            "服务繁忙",
            "overloaded",
            "overload",
            "capacity",
            "high demand",
            "too many concurrent",
            "并发",
            "限流",
            "频率超限",
            "rate limit",
            "too many requests",
            "429",
        )
        if any(m in error_lower or m in error_text for m in busy_markers):
            return "rate_limit"
        if "timeout" in error_lower or "timed out" in error_lower:
            return "timeout"
        return "unknown"

    async def async_post_call_failure_hook(
        self,
        request_data: dict,
        original_exception: Exception,
        user_api_key_dict: Any,
        traceback_str: Optional[str] = None,
    ) -> None:
        """请求失败后的拦截点——只做日志分类，不改写异常。

        v2 修复：
          · 签名增加 user_api_key_dict 和 traceback_str 参数（1.90.x 变更）
          · 错误分类逻辑提取到 _classify_error()，覆盖更多错误码
        """
        self._stats["errors"] += 1

        model = request_data.get("model", "unknown")
        error_text = str(original_exception)
        error_class = self._classify_error(error_text)

        # LiteLLM 的 deployment 日志回调不会覆盖所有“直连模型最终失败”路径。
        # 在请求级失败钩子再兜底一次；direct-* 可稳定反查到真实渠道，确保
        # 限时优先模型的 429/401 同样进入运行期冷却。
        if error_class not in {"cooldown_active", "router_exhausted"}:
            for display_id in self._failure_display_ids(
                request_data, original_exception, error_class,
            ):
                await quota_manager.mark_failure(display_id, error_class)

        # 上游错误有时会回显 URL、请求片段或鉴权信息，日志只保留
        # 异常类型和脱敏分类，不记录 str(original_exception) 原文。
        log_msg = (
            f"[ai-gateway-matrix] model={model} 调用失败，分类={error_class}，"
            f"异常类型={type(original_exception).__name__}"
        )

        if error_class == "router_exhausted":
            logger.critical(
                "%s —— 所有渠道（免费池+升级池）同时不可用，矩阵整体过载或全部限流/预算耗尽中。"
                "这不是单个渠道的偶发问题，建议检查是不是哪个渠道长期卡在冷却状态没有恢复。",
                log_msg,
            )
        elif error_class == "auth_error":
            logger.error(
                "%s —— 鉴权失败，疑似某个渠道的 key 失效/被吊销/填错了。"
                "这种情况【等待不会自愈】，需要去对应供应商后台检查 key 状态。",
                log_msg,
            )
        elif error_class in {"quota_zero", "quota_error"}:
            logger.warning(
                "%s —— 触发额度/计费类错误（疑似配额耗尽而非临时限流），"
                "通常要等到供应商的重置周期（按日/按月）才会恢复。",
                log_msg,
            )
        elif error_class == "rate_limit":
            logger.info(
                "%s —— 临时拥挤/限流（如智谱「访问量过大」）。"
                "本次换其它渠道；短冷却后仍会再试该渠道，不会一次失败就长期弃用。",
                log_msg,
            )
        elif error_class == "timeout":
            logger.warning(
                "%s —— 请求超时，可能是上游响应过慢或网络问题。",
                log_msg,
            )
        else:
            logger.info(
                "%s —— 常规失败，交给 Router 的 cooldown+fallback 自动处理。",
                log_msg,
            )

        return None  # 不改写异常，只是加日志分类

    # ──────────────────────────────────────────────────────────────
    #  统计 & 调试
    # ──────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """返回当前统计快照（可用于 /health 或调试端点）。"""
        return dict(self._stats)


# 模块级单例，config.yaml 里通过 gateway.custom_router_hook.proxy_handler_instance 引用
proxy_handler_instance = ComplexityRouterHook()
