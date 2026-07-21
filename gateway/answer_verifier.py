#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回答质量检验（白嫖友好）
————————————————————————————————————————
目标：尽量用免费上游作答，但挡住空响应 / 乱码 / 明显垃圾。

策略（省专用 API）：
  1. **local**：纯规则，零调用（空输出、模板泄漏、乱码、复读…）
  2. **hybrid（默认）**：规则先拦；仅当「可疑但不确定」时，才用
     与分诊相同的专用稳定 API 做一次极短 JSON 判定。
  3. **off**：关闭（不推荐）

专用判定与分诊共用 CLASSIFIER_* / CLASSIFIER_SOURCE_ENV，
并单独用 verifier_rpm 限额，避免检验把分诊额度打爆。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger("ai_gateway_matrix.answer_verifier")

VERIFY_MODE = (os.environ.get("ANSWER_VERIFY_MODE") or "hybrid").strip().lower()
VERIFY_RPM = max(1, int(os.environ.get("ANSWER_VERIFY_RPM") or "20"))
VERIFY_TIMEOUT = float(os.environ.get("ANSWER_VERIFY_TIMEOUT_SECONDS") or "8")
# 0 = 只在 soft_suspect 时调用；>0 时对免费渠道按概率抽检（仍受 RPM 限制）
VERIFY_SAMPLE_RATE = float(os.environ.get("ANSWER_VERIFY_SAMPLE_RATE") or "0")

_UPSTREAM_ERROR_HINTS = re.compile(
    r"(?:"
    r"rate\s*limit|too many requests|quota\s*(?:exceeded|exhausted)|"
    r"capacity|overloaded|service\s*unavailable|internal\s*server\s*error|"
    r"model\s*(?:is\s*)?(?:not\s*found|unavailable|does not exist)|"
    r"invalid\s*api\s*key|unauthorized|permission\s*denied|"
    r"无可用|额度不足|限流|负载已满|请求过于频繁|服务暂不可用|"
    r"currently\s*unavailable|try\s*again\s*later|bad\s*gateway"
    r")",
    re.IGNORECASE,
)

_VERIFY_SYSTEM = """你是回答质检器。判断「助手回复」是否是对用户提问的有效回答。
无效包括：空话、乱码、模板泄漏、明显报错原文、完全答非所问、无意义复读。
不要评价观点对错，只判断是否像一次可用的模型输出。
只输出 JSON：{"ok":true} 或 {"ok":false,"reason":"brief"}"""


def verify_mode() -> str:
    mode = (os.environ.get("ANSWER_VERIFY_MODE") or VERIFY_MODE or "hybrid").strip().lower()
    if mode in {"off", "local", "hybrid", "dedicated"}:
        return mode
    return "hybrid"


def soft_suspect(prompt: str, output: str) -> Optional[str]:
    """本地廉价「可疑」信号：不直接判死刑，仅决定是否值得花专用 API。"""
    text = (output or "").strip()
    if not text:
        return "empty"
    if _UPSTREAM_ERROR_HINTS.search(text) and len(text) < 400:
        return "upstream_error_text"
    # 过长提问却极短回答（且不像明确短答：是/否/数字）
    prompt_len = len((prompt or "").strip())
    if prompt_len >= 80 and len(text) <= 12:
        if not re.fullmatch(r"(?i)(?:是|否|对|错|yes|no|true|false|ok|[-+]?\d+(?:\.\d+)?)", text):
            return "too_short"
    # 字符种类极少且偏长 → 疑似复读/垃圾
    if len(text) >= 40:
        unique = len(set(text))
        if unique <= 4:
            return "low_entropy"
    # 同一短句重复占比过高
    parts = re.findall(r".{8,40}", text)
    if len(parts) >= 4:
        from collections import Counter
        top = Counter(parts).most_common(1)[0][1]
        if top >= max(3, len(parts) // 2):
            return "repeat_chunks"
    return None


def _parse_ok(raw: str) -> bool:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text[3:]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
        closing = text.rfind("```")
        if closing >= 0:
            text = text[:closing]
        text = text.strip()
    starts = [i for tok in ("{", "[") if (i := text.find(tok)) >= 0]
    if not starts:
        raise ValueError("no json")
    parsed, _ = json.JSONDecoder().raw_decode(text[min(starts):])
    if isinstance(parsed, list):
        parsed = next((x for x in parsed if isinstance(x, dict)), None)
    if not isinstance(parsed, dict):
        raise TypeError("not object")
    ok = parsed.get("ok")
    if isinstance(ok, bool):
        return ok
    if isinstance(ok, str):
        return ok.strip().lower() in {"1", "true", "yes", "ok"}
    raise ValueError("missing ok")


async def dedicated_verify(prompt: str, output: str) -> Optional[bool]:
    """用专用分诊 API 做一次极短质检。

    返回:
      True  — 通过
      False — 明确失败
      None  — 无后端 / 额度满 / 调用失败（调用方应放行，避免误杀）
    """
    from . import llm_classifier, quota_manager, usage_tracker

    backends = llm_classifier.resolve_classifier_backends()
    if not backends:
        return None
    backend = backends[0]
    cred = backend["cred_name"]
    reserved = await quota_manager.reserve_limits(
        [(f"credential:{cred}:verifier_rpm", VERIFY_RPM, 60)]
    )
    if not reserved:
        logger.info("[ai-gateway-matrix] 答检专用额度已满，跳过 LLM 检验")
        return None

    # 截断：质检不需要全文
    p = (prompt or "")[:600]
    o = (output or "")[:800]
    kwargs: dict[str, Any] = {
        "model": backend["model"],
        "api_key": backend["api_key"],
        "messages": [
            {"role": "system", "content": _VERIFY_SYSTEM},
            {"role": "user", "content": f"用户提问：\n{p}\n\n助手回复：\n{o}"},
        ],
        "max_tokens": 48,
        "temperature": 0,
        "timeout": VERIFY_TIMEOUT,
        "response_format": {"type": "json_object"},
    }
    if backend.get("api_base"):
        kwargs["api_base"] = backend["api_base"]

    usage_id = usage_tracker.make_usage_key(
        backend["model"],
        backend.get("api_base"),
        backend["api_key"],
        env_var=backend.get("cred_name"),
    )
    try:
        import litellm  # 延迟导入：单元测试不必装 litellm 也能验 soft_suspect
        response = await litellm.acompletion(**kwargs)
        raw = response.choices[0].message.content or ""
        ok = _parse_ok(raw)
        usage = getattr(response, "usage", None)
        await usage_tracker.record_call(
            usage_id,
            success=True,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )
        logger.info(
            "[ai-gateway-matrix] 答检: ok=%s（后端=%s）",
            ok, backend.get("label"),
        )
        return ok
    except Exception as exc:
        await usage_tracker.record_call(usage_id, success=False)
        logger.warning(
            "[ai-gateway-matrix] 答检调用失败（%s），放行以免误杀: %s",
            backend.get("label"), type(exc).__name__,
        )
        return None


async def should_reject_with_llm(
    prompt: str,
    output: str,
    *,
    force_sample: bool = False,
) -> Optional[str]:
    """若应拒绝，返回 reason 字符串；否则 None。

    force_sample：调用方决定是否触发抽检（例如免费观察期渠道）。
    """
    mode = verify_mode()
    if mode in {"off", "local"}:
        return None

    suspect = soft_suspect(prompt, output)
    need = bool(suspect) or force_sample
    if mode == "dedicated":
        need = True
    if not need:
        return None

    result = await dedicated_verify(prompt, output)
    if result is False:
        return suspect or "llm_reject"
    return None
