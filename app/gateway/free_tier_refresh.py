#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
免费额度跟进更新
————————————————————————————————————————
免费 RPM/RPD/TPD 经常变。本模块：

  1. 拉取各厂限额文档（HTML 文本）
  2. 与当前 state 中的限额对比
  3. 可选：调用顶级模型解析文档，抽出 windows / note_zh
  4. 写入 state/free-tier-quotas.json，供 dashboard 合并展示

不直接改 quota_catalog.py 源码（避免 git 冲突）；运行时覆盖。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("ai_gateway_matrix.free_tier_refresh")

_ROOT = Path(__file__).resolve().parent.parent
_STATE_PATH = Path(
    os.environ.get(
        "FREE_TIER_QUOTA_STATE",
        str(_ROOT / "state" / "free-tier-quotas.json"),
    )
)
_ENABLE = (os.environ.get("FREE_TIER_REFRESH_ENABLE", "true") or "true").lower() not in {
    "0",
    "false",
    "no",
    "off",
}
_USE_LLM = (os.environ.get("FREE_TIER_REFRESH_LLM", "true") or "true").lower() not in {
    "0",
    "false",
    "no",
    "off",
}
_MIN_CONF = float(os.environ.get("FREE_TIER_REFRESH_MIN_CONF", "0.65") or "0.65")
_FETCH_TIMEOUT = 25
_MAX_DOC_CHARS = 14000

# env_var → 限额文档 URL（可扩展）
FREE_TIER_DOC_SOURCES: dict[str, dict[str, str]] = {
    "GROQ_API_KEY": {
        "docs_url": "https://console.groq.com/docs/rate-limits",
        "name": "Groq",
    },
    "GEMINI_API_KEY": {
        "docs_url": "https://ai.google.dev/gemini-api/docs/rate-limits",
        "name": "Google Gemini",
    },
    "CEREBRAS_API_KEY": {
        "docs_url": "https://inference-docs.cerebras.ai/support/rate-limits",
        "name": "Cerebras",
    },
    "OPENROUTER_API_KEY": {
        "docs_url": "https://openrouter.ai/docs/api-reference/limits",
        "name": "OpenRouter",
    },
    "SAMBANOVA_API_KEY": {
        "docs_url": "https://docs.sambanova.ai/cloud/docs/get-started/rate-limits",
        "name": "SambaNova",
    },
    "MODELSCOPE_API_KEY": {
        "docs_url": "https://modelscope.cn/docs/model-service/API-Inference/limits",
        "name": "ModelScope",
    },
    "CLOUDFLARE_API_TOKEN": {
        "docs_url": "https://developers.cloudflare.com/workers-ai/platform/pricing/",
        "name": "Cloudflare Workers AI",
    },
    "SILICONFLOW_API_KEY": {
        "docs_url": "https://docs.siliconflow.cn/",
        "name": "SiliconFlow",
    },
    "MISTRAL_KEY_1": {
        "docs_url": "https://docs.mistral.ai/deployment/laplateforme/tier",
        "name": "Mistral",
    },
    "NVIDIA_API_KEY": {
        "docs_url": "https://docs.api.nvidia.com/",
        "name": "NVIDIA NIM",
    },
    "GLM_API_KEY": {
        "docs_url": "https://docs.bigmodel.cn/cn/guide/start/model-overview",
        "name": "智谱 GLM",
    },
}

_SYSTEM = """你是免费 LLM API 额度审计员。根据厂商文档摘录，提取当前免费层/试用层的限额。
只输出 JSON，不要 Markdown：
{
  "note_zh": "一句话中文说明（含是否可重置）",
  "free_kind": "resettable|once|trial|paid|unknown",
  "confidence": 0.0到1.0,
  "windows": [
    {"id":"rpm","metric":"requests","window_sec":60,"limit":30,"label_zh":"每分钟请求"},
    {"id":"rpd","metric":"requests","window_sec":86400,"limit":1000,"label_zh":"每日请求"}
  ]
}
规则：
- window_sec: 60=分钟, 3600=小时, 86400=天, 2592000≈月
- metric: requests 或 tokens
- limit 只填数字；文档说「动态/控制台」则 limit=null
- 优先免费层数字；若文档只写试用金，free_kind=trial，windows 可空
- 拿不准 confidence 放低，windows 宁缺勿造
"""


def load_overrides() -> dict[str, Any]:
    try:
        if _STATE_PATH.is_file():
            data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, ValueError):
        pass
    return {"updated_at": None, "providers": {}}


def save_overrides(data: dict[str, Any]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(_STATE_PATH)


def get_provider_override(env_var: str) -> Optional[dict[str, Any]]:
    """供 quota_catalog 合并：返回 {note_zh, windows, free_kind, source, checked_at}。"""
    if not env_var:
        return None
    data = load_overrides()
    providers = data.get("providers") or {}
    hit = providers.get(env_var)
    if isinstance(hit, dict) and hit.get("windows") is not None:
        return hit
    # 账号后缀
    m = re.match(r"^(.*)_(\d+)$", env_var)
    if m:
        hit = providers.get(m.group(1)) or providers.get(f"{m.group(1)}_1")
        if isinstance(hit, dict):
            return hit
    return None


def _fetch_doc_text(url: str) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "AI-Gateway-Matrix-FreeTierRefresh/1.0",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
        },
        method="GET",
    )
    try:
        with urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        logger.info("[free-tier] 拉取文档失败 %s: %s", url, type(exc).__name__)
        return ""
    # 粗剥 HTML
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", raw)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&amp;|&lt;|&gt;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > _MAX_DOC_CHARS:
        text = text[: _MAX_DOC_CHARS // 2] + "\n[...]\n" + text[-_MAX_DOC_CHARS // 2 :]
    return text


def _heuristic_extract(text: str) -> dict[str, Any]:
    """无 LLM 时的正则兜底：抓 RPM/RPD/TPD 常见写法。"""
    windows: list[dict[str, Any]] = []
    patterns = [
        (r"(\d[\d,]*)\s*RPM", "rpm", "requests", 60, "每分钟请求 (RPM)"),
        (r"(\d[\d,]*)\s*RPD", "rpd", "requests", 86400, "每日请求 (RPD)"),
        (r"(\d[\d,]*)\s*TPM", "tpm", "tokens", 60, "每分钟 tokens (TPM)"),
        (r"(\d[\d,]*)\s*TPD", "tpd", "tokens", 86400, "每日 tokens (TPD)"),
        (r"(\d[\d,]*)\s*requests?\s*/\s*minute", "rpm", "requests", 60, "每分钟请求"),
        (r"(\d[\d,]*)\s*requests?\s*/\s*day", "rpd", "requests", 86400, "每日请求"),
        (r"(\d[\d,]*)\s*次\s*/\s*天", "rpd", "requests", 86400, "每日请求"),
        (r"(\d[\d,]*)\s*次/日", "rpd", "requests", 86400, "每日请求"),
        (r"(\d[\d,]*)\s*Neurons?\s*/?\s*day", "neurons_day", "tokens", 86400, "每日 Neurons"),
    ]
    seen = set()
    for pat, wid, metric, wsec, label in patterns:
        m = re.search(pat, text, re.I)
        if not m or wid in seen:
            continue
        try:
            limit = int(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if limit <= 0 or limit > 10_000_000:
            continue
        seen.add(wid)
        windows.append({
            "id": wid,
            "metric": metric,
            "window_sec": wsec,
            "limit": limit,
            "source": "refresh_heuristic",
            "label_zh": label,
        })
    free_kind = "unknown"
    low = text.lower()
    if any(x in low for x in ("per day", "daily", "每日", "reset", "重置")):
        free_kind = "resettable"
    if any(x in low for x in ("one-time", "trial credit", "一次性", "试用额度")):
        free_kind = "trial" if "trial" in low or "试用" in text else "once"
    return {
        "note_zh": "启发式从文档摘录（未用大模型）",
        "free_kind": free_kind,
        "confidence": 0.45 if windows else 0.2,
        "windows": windows,
    }


async def _llm_extract(provider_name: str, docs_url: str, text: str) -> Optional[dict[str, Any]]:
    if not _USE_LLM or not text:
        return None
    try:
        from gateway.llm_classifier import resolve_classifier_backend
        import litellm
    except Exception:
        return None
    backend = resolve_classifier_backend()
    if not backend:
        return None
    # 优先用更强模型：若 env 有 GENERALCOMPUTE 已在 resolve 里靠前
    user = (
        f"厂商：{provider_name}\n文档 URL：{docs_url}\n\n"
        f"文档摘录：\n{text[:12000]}\n"
    )
    kwargs: dict[str, Any] = {
        "model": backend["model"],
        "api_key": backend["api_key"],
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
        "max_tokens": 500,
        "temperature": 0,
        "timeout": 45,
    }
    if backend.get("api_base"):
        kwargs["api_base"] = backend["api_base"]
    try:
        try:
            resp = await litellm.acompletion(**kwargs, response_format={"type": "json_object"})
        except Exception:
            resp = await litellm.acompletion(**kwargs)
        raw = (resp.choices[0].message.content or "").strip()
        if "{" in raw and "}" in raw:
            raw = raw[raw.index("{") : raw.rindex("}") + 1]
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        windows = data.get("windows") or []
        cleaned = []
        for w in windows:
            if not isinstance(w, dict):
                continue
            wid = str(w.get("id") or "limit")
            metric = w.get("metric") if w.get("metric") in ("requests", "tokens") else "requests"
            try:
                wsec = int(w.get("window_sec") or 86400)
            except (TypeError, ValueError):
                wsec = 86400
            lim = w.get("limit")
            if lim is not None:
                try:
                    lim = int(lim)
                except (TypeError, ValueError):
                    lim = None
            cleaned.append({
                "id": wid,
                "metric": metric,
                "window_sec": wsec,
                "limit": lim,
                "source": "refresh_llm",
                "label_zh": str(w.get("label_zh") or wid),
            })
        conf = float(data.get("confidence") or 0)
        return {
            "note_zh": str(data.get("note_zh") or "")[:500],
            "free_kind": str(data.get("free_kind") or "unknown"),
            "confidence": conf,
            "windows": cleaned,
            "llm_model": backend.get("label") or backend.get("model"),
        }
    except Exception as exc:
        logger.info("[free-tier] LLM 解析失败: %s", type(exc).__name__)
        return None


async def refresh_one(env_var: str, meta: dict[str, str]) -> dict[str, Any]:
    docs_url = meta.get("docs_url") or ""
    name = meta.get("name") or env_var
    text = await asyncio.to_thread(_fetch_doc_text, docs_url) if docs_url else ""
    result: dict[str, Any] = {
        "env_var": env_var,
        "name": name,
        "docs_url": docs_url,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fetch_ok": bool(text),
    }
    if not text:
        result["error"] = "doc_fetch_failed"
        return result

    llm = await _llm_extract(name, docs_url, text)
    heur = _heuristic_extract(text)
    chosen = None
    if llm and float(llm.get("confidence") or 0) >= _MIN_CONF:
        chosen = llm
        result["method"] = "llm"
    elif llm and heur.get("windows") and float(llm.get("confidence") or 0) >= 0.4:
        # 合并：LLM note + 启发式 windows 若 LLM windows 空
        if not llm.get("windows") and heur.get("windows"):
            llm = {**llm, "windows": heur["windows"], "method": "llm+heuristic"}
        chosen = llm
        result["method"] = llm.get("method") or "llm_low_conf"
    elif heur.get("windows"):
        chosen = heur
        result["method"] = "heuristic"
    else:
        result["method"] = "none"
        result["error"] = "no_limits_extracted"
        return result

    conf = float(chosen.get("confidence") or 0)
    result["confidence"] = conf
    result["free_kind"] = chosen.get("free_kind") or "unknown"
    result["note_zh"] = chosen.get("note_zh") or ""
    result["windows"] = chosen.get("windows") or []
    if chosen.get("llm_model"):
        result["llm_model"] = chosen["llm_model"]
    result["applied"] = conf >= _MIN_CONF or result["method"] == "heuristic"
    return result


async def refresh_all(env_vars: Optional[list[str]] = None) -> dict[str, Any]:
    if not _ENABLE:
        return {"enabled": False, "providers": {}}

    sources = FREE_TIER_DOC_SOURCES
    if env_vars:
        sources = {k: v for k, v in sources.items() if k in env_vars}

    prev = load_overrides()
    providers = dict(prev.get("providers") or {})
    report: list[dict[str, Any]] = []

    for env_var, meta in sources.items():
        try:
            one = await refresh_one(env_var, meta)
        except Exception as exc:
            one = {
                "env_var": env_var,
                "error": f"{type(exc).__name__}: {exc}",
                "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "applied": False,
            }
        report.append(one)
        if one.get("applied") and one.get("windows") is not None:
            providers[env_var] = {
                "note_zh": one.get("note_zh") or "",
                "docs_url": one.get("docs_url") or meta.get("docs_url"),
                "free_kind": one.get("free_kind") or "unknown",
                "windows": one.get("windows") or [],
                "source": f"auto_{one.get('method')}",
                "confidence": one.get("confidence"),
                "checked_at": one.get("checked_at"),
                "llm_model": one.get("llm_model"),
            }
            logger.info(
                "[free-tier] 已更新 %s method=%s conf=%.2f windows=%d",
                env_var,
                one.get("method"),
                float(one.get("confidence") or 0),
                len(one.get("windows") or []),
            )
        else:
            logger.info(
                "[free-tier] 跳过写入 %s method=%s err=%s",
                env_var,
                one.get("method"),
                one.get("error"),
            )

    out = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "providers": providers,
        "last_run": report,
    }
    save_overrides(out)
    return out
