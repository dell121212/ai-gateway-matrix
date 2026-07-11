#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
复杂度启发式路由 Hook (v4 — 引入指定模型分类)
————————————————————————————————————————
挂载在 LiteLLM 的 async_pre_call_hook 上：每次请求真正发给某个渠道之前，
先看一眼任务内容，决定改写 data["model"] 为 "fast-pool" / "free-pool" /
"strong-model-pool" / "trusted-pool" 之一。

设计原则（v4 更新）：
  · 敏感内容检测和"极短输入直接走快速池"这两条仍然是纯规则、零延迟、
    零成本，因为没必要为了这两种情况多付一次网络往返。
  · 除此之外，任务档位判断交给 gateway/llm_classifier.py 里指定的模型
    （Groq Llama-3.3-70B）来做，而不是纯靠关键词/长度猜——这是
    你要的"任务类别先交给指定模型判断"。
  · decide_pool() 里原来那套关键词/正则/token数启发式规则完整保留，
    但角色从"主路径"降级为"分类器不可用时的兜底"：分类器超时、
    报错、或返回格式不对，都会自动回退到这套规则，不会让请求失败。
  · 这一层只负责"要不要升级"，"免费池里具体用哪个渠道"交给
    LiteLLM Router 的 RPM/预算/优先级逻辑去处理，两者不重叠。
  · 跟 config.yaml 里的 context_window_fallbacks（按 token 数硬性触发）
    是互补关系：这里处理"分类器/关键词/长度判断出来的复杂度"，token 数
    兜底处理"分类器和启发式都没判断出来、但内容其实超长"的漏判。

══════════════════════════════════════════════════════════════════════
v4 新增（引入指定模型分类）：
  1. 新增 gateway/llm_classifier.py，指定 Groq Llama-3.3-70B 作为专职分类模型，
     用"具名档位 + 一句话判据"的 prompt（参照 NVIDIA-AI-Blueprints/
     llm-router 蓝图的做法）判断任务属于 弱/中/强 中的哪一档。
  2. 新增 decide_pool_with_classifier()，作为请求时真正使用的入口：
     敏感检测 → 极短输入直接 fast-pool → 分类器判断 → 分类器失败则回退
     decide_pool() 里的启发式规则。
  3. decide_pool() 本身不变，继续保留给 scripts/test_gateway.py 的离线结构性
     自检使用（不需要真实网络/API key 就能验证规则逻辑本身没写错）。
  4. 新增 stats 计数器：classifier_used / classifier_fallback_to_heuristic /
     classifier_skipped_trivial，方便观察分类器实际命中率和降级频率。

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

import logging
import os
import re
from typing import Any, Optional

import litellm
from litellm.integrations.custom_logger import CustomLogger

from . import (
    llm_classifier,
    optimal_channels,
    pricing,
    provider_registry,
    quota_manager,
    usage_tracker,
)

logger = logging.getLogger("ai_gateway_matrix.router_hook")

FAST_POOL = "fast-pool"
FREE_POOL = "free-pool"
STRONG_POOL = "strong-model-pool"
TRUSTED_POOL = "trusted-pool"
AUTO_ROUTE = "auto-route"

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

# 触发升级到 strong-model-pool 的阈值
TOKEN_THRESHOLD = 8000       # 粗估 token 数超过此值则升级
CHAR_THRESHOLD = 30000       # 字符数超过此值则升级（token 估算的 fallback）
FILE_COUNT_THRESHOLD = 5     # 提到超过 N 个文件则升级

# 路由到 fast-pool 的阈值（短输入走超快推理 Groq/Cerebras）
FAST_TOKEN_THRESHOLD = 200   # 粗估 token 数低于此值 → 快速池
FAST_CHAR_THRESHOLD = 600    # 字符数低于此值 → 快速池


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
            "errors": 0,
        }
        # "限时优先"功能（v6）需要知道每个渠道的 rpm 上限和它对应的
        # direct-xxxxxxxxxx model_name，这些信息只存在于 config.yaml，
        # 启动时解析一次、缓存在内存里，避免每次请求都重新读文件。
        # 解析失败（文件不存在/格式不对）不应该让整个网关起不来，
        # 只是让"限时优先"功能自动失效，退回正常的池子路由。
        self._channel_registry: dict[str, dict] = {}
        self._provider_registry: Optional[provider_registry.ProviderRegistry] = None
        self._load_channel_registry()

    def _load_channel_registry(self) -> None:
        """解析 config.yaml，为 fast/free/strong-model-pool 里的每个 deployment
        建立 display_id -> {model, api_base, env_var, rpm_limit, direct_model_name}
        的映射，供"限时优先"功能查找用。"""
        try:
            self._provider_registry = provider_registry.load_registry()
            self._channel_registry = dict(self._provider_registry.channels)
        except Exception as exc:
            logger.warning(
                "[ai-gateway-matrix] 无法加载供应商注册表（%s: %s），"
                "能力过滤/限时优先将不可用",
                type(exc).__name__, exc,
            )
            return
        logger.info("[ai-gateway-matrix] 渠道注册表加载完成，共 %d 个渠道", len(self._channel_registry))

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

        # 规则 4：token 数超过阈值 → 升级到 strong-model-pool
        token_count = self._estimate_token_count(text)
        if token_count >= TOKEN_THRESHOLD:
            logger.info(
                "[ai-gateway-matrix] 升级到 strong-model-pool（粗估 %d tokens，超过阈值 %d）",
                token_count, TOKEN_THRESHOLD
            )
            return STRONG_POOL

        # 规则 5：字符数超过阈值（token 估算的 fallback）→ 升级到 strong-model-pool
        if len(text) >= CHAR_THRESHOLD:
            logger.info(
                "[ai-gateway-matrix] 升级到 strong-model-pool（%d 字符，超过阈值 %d）",
                len(text), CHAR_THRESHOLD
            )
            return STRONG_POOL

        # 规则 6：超短输入 → 快速池（Groq/Cerebras 超快推理）
        if token_count < FAST_TOKEN_THRESHOLD and len(text) < FAST_CHAR_THRESHOLD:
            logger.info(
                "[ai-gateway-matrix] 路由到 fast-pool（%d tokens / %d 字符，短输入走快速推理）",
                token_count, len(text)
            )
            return FAST_POOL

        # 默认：留在免费池
        return FREE_POOL

    @staticmethod
    def _is_configured(channel: dict) -> bool:
        env_var = channel.get("env_var")
        value = os.environ.get(env_var or "", "").strip()
        return bool(value) and not value.startswith("dummy-")

    async def _resolve_capability_target(self, pool: str, requirements: set[str]) -> str:
        if requirements == {"text"}:
            return pool
        if self._provider_registry is None:
            raise RuntimeError("供应商能力注册表不可用，拒绝对工具/多模态请求猜测路由")
        candidates = [
            channel for channel in self._provider_registry.candidates(pool, requirements)
            if self._is_configured(channel)
        ]
        selected = await quota_manager.choose_and_reserve(candidates)
        if selected is None:
            required = ", ".join(sorted(requirements - {"text"}))
            raise RuntimeError(f"没有已配置且仍有额度的渠道支持请求能力: {required}")
        return selected["direct_model_name"]

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
        if requirements == {"text"}:
            return TRUSTED_POOL
        candidates = [
            channel
            for channel in self._provider_registry.sensitive_candidates(requirements)
            if self._is_configured(channel)
        ] if self._provider_registry is not None else []
        selected = await quota_manager.choose_and_reserve(candidates)
        if selected is None:
            raise RuntimeError("敏感请求需要的能力没有符合数据政策的可用渠道")
        return selected["direct_model_name"]

    async def _pick_optimal_channel(self, requirements: set[str]) -> Optional[str]:
        """检查是否存在仍然有效的"限时优先"渠道，有就返回它的直连 model_name。

        "有效"指：还没过期（Redis key 的 TTL 保证了这一点，list_optimal()
        只会返回没过期的），而且这一分钟还没打满 RPM。多个标记同时存在时，
        按最快过期的优先尝试，都打满了才放弃、回退到正常的池子路由。
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

    async def decide_pool_with_classifier(self, data: dict) -> str:
        """请求时真正使用的路由决策入口（v6 更新）。

        按优先级：
          1. 敏感内容检测（不经过分类器/限时优先，命中就直接 trusted-pool——
             隐私优先于一切，包括"这个渠道额度快过期了，能省则省"这种
             成本考虑）
          2. 限时优先渠道检查：如果有渠道被标记为"限时优先"且还没过期、
             这分钟还有 RPM 余量 → 无条件路由到它，不管这个请求原本该
             分类到弱/中/强哪一档（这是"快过期的额度/活动额度，先烧完
             再说"的行为）
          3. 关键词/正则/文件数快速判断（零成本，命中就直接 strong-model-pool，
             不管文本长短）
          4. 极短输入且没命中规则 3 → 直接给 fast-pool
          5. 交给 llm_classifier 指定的模型判断档位
          6. 分类器超时/出错/返回格式不对 → 回退到 decide_pool() 里的
             纯规则启发式
        """
        text = self._extract_text(data)
        requirements = (
            self._provider_registry.request_requirements(data)
            if self._provider_registry is not None else {"text"}
        )
        sensitive_target = await self._sensitive_target(data, requirements)
        if sensitive_target is not None:
            return sensitive_target

        if not text:
            return await self._resolve_capability_target(FAST_POOL, requirements)

        optimal_target = await self._pick_optimal_channel(requirements)
        if optimal_target is not None:
            self._stats["routed_to_optimal"] += 1
            return optimal_target

        text_lower = text.lower()
        quick_result = self._quick_escalation_check(text, text_lower)
        if quick_result is not None:
            return await self._resolve_capability_target(quick_result, requirements)

        token_count = self._estimate_token_count(text)
        # 长度是硬约束，必须在只看前 2000 字符的外部分类器之前生效，
        # 避免长请求被截断样本误判成弱档。
        if token_count >= TOKEN_THRESHOLD or len(text) >= CHAR_THRESHOLD:
            return await self._resolve_capability_target(STRONG_POOL, requirements)
        if token_count < FAST_TOKEN_THRESHOLD and len(text) < FAST_CHAR_THRESHOLD:
            self._stats["classifier_skipped_trivial"] += 1
            logger.info(
                "[ai-gateway-matrix] 输入过短（%d tokens / %d 字符），跳过分类器直接走 fast-pool",
                token_count, len(text),
            )
            return await self._resolve_capability_target(FAST_POOL, requirements)

        pool = await llm_classifier.classify_task(text)
        if pool is not None:
            self._stats["classifier_used"] += 1
            return await self._resolve_capability_target(pool, requirements)

        # 分类器不可用/失败 → 回退到纯规则启发式（decide_pool 内部会重新走一遍
        # 敏感检测/关键词/正则/文件数/token 数判断；这里不重复造轮子）
        self._stats["classifier_fallback_to_heuristic"] += 1
        fallback_pool = self.decide_pool(data)
        return await self._resolve_capability_target(fallback_pool, requirements)

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

        requested_model = data.get("model", "")

        # 显式指定内部池时仍必须执行敏感数据政策；复杂度分档只属于 auto-route。
        if requested_model != AUTO_ROUTE:
            if requested_model in {FAST_POOL, FREE_POOL, STRONG_POOL} or str(
                requested_model
            ).startswith("direct-"):
                requirements = (
                    self._provider_registry.request_requirements(data)
                    if self._provider_registry is not None else {"text"}
                )
                sensitive_target = await self._sensitive_target(data, requirements)
                if sensitive_target is not None:
                    data["model"] = sensitive_target
                    self._stats["routed_to_trusted_sensitive"] += 1
            return data

        target_pool = await self.decide_pool_with_classifier(data)
        data["model"] = target_pool

        if target_pool == TRUSTED_POOL:
            self._stats["routed_to_trusted_sensitive"] += 1
        elif target_pool.startswith("direct-"):
            pass  # 限时优先命中，routed_to_optimal 已经在 decide_pool_with_classifier 里计过了
        elif target_pool == STRONG_POOL:
            self._stats["escalated_to_strong"] += 1
        elif target_pool == FAST_POOL:
            self._stats["routed_to_fast"] += 1
        else:
            self._stats["routed_to_free"] += 1

        logger.info(
            "[ai-gateway-matrix] 路由决策: auto-route → %s (call_type=%s)",
            target_pool, call_type,
        )

        return data

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
        litellm_params = kwargs.get("litellm_params") or {}
        model = litellm_params.get("model")
        if not model:
            return None
        api_base = litellm_params.get("api_base")
        api_key = litellm_params.get("api_key")
        return usage_tracker.make_channel_id(model, api_base, api_key)

    def _extract_display_id(self, kwargs: dict) -> Optional[str]:
        """将 LiteLLM 已选中的 deployment 反查回不含密钥的稳定标识。"""
        params = kwargs.get("litellm_params") or {}
        model = params.get("model")
        api_base = params.get("api_base")
        if not model:
            return None
        matches = [
            channel for channel in self._channel_registry.values()
            if channel.get("model") == model and channel.get("api_base") == api_base
        ]
        if len(matches) == 1:
            return matches[0]["display_id"]
        resolved_key = params.get("api_key")
        if resolved_key is not None:
            resolved_key = str(resolved_key)
            for channel in matches:
                env_value = os.environ.get(channel.get("env_var") or "")
                if env_value and env_value == resolved_key:
                    return channel["display_id"]
        return None

    async def async_log_success_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
        """请求成功后记一笔用量——只做统计，任何异常都不应该往外抛。

        v7 新增：顺手记一笔 token 消耗和预计花费。花费计算（先查 litellm
        内置价格库，查不到再估算，都查不到就是 None）在 gateway/pricing.py 里，
        这里只负责把 usage 里的 token 数掏出来、调一下 pricing、
        再交给 usage_tracker 存起来——任何一步失败都不应该影响真实请求
        已经成功返回这件事，所以整段包在 try/except 里。
        """
        channel_id = self._extract_channel_id(kwargs)
        if not channel_id:
            return
        display_id = self._extract_display_id(kwargs)

        prompt_tokens = 0
        completion_tokens = 0
        cost: Optional[float] = None
        cost_source = "unknown"
        latency_ms: Optional[float] = None
        try:
            usage = getattr(response_obj, "usage", None)
            if usage is not None:
                prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                completion_tokens = getattr(usage, "completion_tokens", 0) or 0
            model = (kwargs.get("litellm_params") or {}).get("model", "")
            cost, cost_source = pricing.compute_cost(model, response_obj, prompt_tokens, completion_tokens)
            if start_time is not None and end_time is not None:
                delta = end_time - start_time
                latency_ms = float(delta.total_seconds() * 1000) if hasattr(delta, "total_seconds") else None
        except Exception as exc:
            logger.debug("[ai-gateway-matrix] 提取 token/花费信息失败（不影响请求本身）: %s", exc)

        await usage_tracker.record_call(
            channel_id, success=True,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            cost=cost, cost_source=cost_source, latency_ms=latency_ms,
        )
        if display_id:
            await quota_manager.mark_success(display_id)

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
        display_id = self._extract_display_id(kwargs)
        if display_id:
            await quota_manager.mark_failure(display_id, error_class)

    # ──────────────────────────────────────────────────────────────
    #  失败分类 & 日志
    # ──────────────────────────────────────────────────────────────

    def _classify_error(self, error_text: str) -> str:
        """把上游错误文本粗略分类，返回类别字符串。

        返回值：
          · "router_exhausted"  所有渠道同时不可用
          · "auth_error"        鉴权失败（key 失效/填错）
          · "quota_error"       额度/计费类错误（配额耗尽）
          · "rate_limit"        临时限流（429）
          · "timeout"           超时
          · "unknown"           其他
        """
        error_lower = error_text.lower()

        if "no deployments available" in error_lower or "all deployments" in error_lower:
            return "router_exhausted"
        if "401" in error_lower or "unauthorized" in error_lower or "invalid api key" in error_lower:
            return "auth_error"
        if "402" in error_lower or "payment required" in error_lower or "quota" in error_lower or "billing" in error_lower:
            return "quota_error"
        if "429" in error_lower or "rate limit" in error_lower or "too many requests" in error_lower:
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
        elif error_class == "quota_error":
            logger.warning(
                "%s —— 触发额度/计费类错误（疑似配额耗尽而非临时限流），"
                "通常要等到供应商的重置周期（按日/按月）才会恢复。",
                log_msg,
            )
        elif error_class == "rate_limit":
            logger.info(
                "%s —— 临时限流（429），交给 Router 的 cooldown+fallback 自动处理。",
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
