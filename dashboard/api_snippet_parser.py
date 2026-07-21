#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从用户粘贴的「官方接口说明 / curl / .env / JSON」里抽取 OpenAI 兼容接入信息。

不做黑盒执行，只做只读正则与结构化抽取；Key 原样返回给仪表盘写入 .env。
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

# 常见 OpenAI 兼容 endpoint
_URL_RE = re.compile(
    r"https?://[^\s\"'<>`]+",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(
    r"(?:Authorization\s*[:=]\s*['\"]?Bearer\s+|Bearer\s+)([A-Za-z0-9_\-.:/=+]{8,})",
    re.IGNORECASE,
)
_KEY_ASSIGN_RE = re.compile(
    r"(?:api[_-]?key|openai_api_key|token|secret[_-]?key|access[_-]?key)"
    r"\s*[:=]\s*['\"]?([A-Za-z0-9_\-.:/=+]{8,})",
    re.IGNORECASE,
)
_ENV_EXPORT_RE = re.compile(
    r"(?:export\s+)?([A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|KEY|SECRET))\s*=\s*['\"]?([^\s'\"#]+)",
)
_MODEL_ASSIGN_RE = re.compile(
    r"(?:#\s*)?(?:[\"']?model[\"']?|model_name)\s*[:=]\s*[\"']?"
    r"([A-Za-z0-9][A-Za-z0-9._:/\-@+]{1,200})"
    r"|--model\s+([A-Za-z0-9][A-Za-z0-9._:/\-@+]{1,200})",
    re.IGNORECASE,
)
_NAME_HINT_RE = re.compile(
    r"(?:provider|vendor|name|服务商|提供方|厂商)\s*[:=：]\s*[\"']?([^\n\"']{2,60})",
    re.IGNORECASE,
)
_CURL_H_RE = re.compile(
    r"""-H\s+['"]([^'"]+)['"]""",
    re.IGNORECASE,
)


def _strip_url_junk(url: str) -> str:
    url = url.rstrip(").,;]'\"")
    return url


def _normalize_base(url: str) -> Optional[str]:
    raw = _strip_url_junk((url or "").strip()).rstrip("/")
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    path = parsed.path or ""
    # 去掉具体 API 路径，保留 /v1 或根
    for suffix in (
        "/chat/completions",
        "/completions",
        "/embeddings",
        "/models",
        "/v1/chat/completions",
    ):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    path = path.rstrip("/")
    # 若用户只给了 host，常见习惯补 /v1
    if path in {"", "/"} and not any(
        h in (parsed.hostname or "")
        for h in ("localhost", "127.0.0.1")
    ):
        # 很多文档已含 /v1；没有时仍返回 host，由上层 normalize 处理
        pass
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def _score_url(url: str) -> int:
    """更像 API base 的 URL 得分更高。"""
    u = url.lower()
    score = 0
    if "/v1" in u:
        score += 5
    if any(x in u for x in ("api.", "/api", "openai", "compatible")):
        score += 3
    if any(x in u for x in ("chat/completions", "/models", "embeddings")):
        score += 4
    if any(x in u for x in ("github.com", "docs.", "readme", "blog.", "medium.com")):
        score -= 8
    if u.endswith((".md", ".html", ".png", ".jpg")):
        score -= 10
    return score


def _guess_provider_name(base: Optional[str], text: str) -> str:
    m = _NAME_HINT_RE.search(text or "")
    if m:
        return m.group(1).strip()[:80]
    if base:
        host = (urlsplit(base).hostname or "").lower()
        host = host.removeprefix("www.").removeprefix("api.")
        # api.foo.com → foo
        parts = [p for p in host.split(".") if p and p not in {"com", "cn", "ai", "io", "net", "org", "co"}]
        if parts:
            return parts[0].replace("-", " ").title()[:80]
    return "自定义免费 API"


def _extract_keys(text: str) -> list[str]:
    found: list[str] = []
    for pattern in (_BEARER_RE, _KEY_ASSIGN_RE):
        for m in pattern.finditer(text or ""):
            val = m.group(1).strip().rstrip("',\"")
            if val.lower() in {"your-api-key", "sk-xxx", "xxx", "changeme", "<token>", "${api_key}"}:
                continue
            if val not in found:
                found.append(val)
    for m in _ENV_EXPORT_RE.finditer(text or ""):
        val = m.group(2).strip().strip("'\"")
        if len(val) >= 8 and val not in found:
            found.append(val)
    # curl -H headers
    for m in _CURL_H_RE.finditer(text or ""):
        header = m.group(1)
        bm = re.search(r"Bearer\s+([A-Za-z0-9_\-.:/=+]{8,})", header, re.I)
        if bm:
            val = bm.group(1).strip()
            if val not in found:
                found.append(val)
    return found


def _extract_models(text: str) -> list[str]:
    found: list[str] = []
    for m in _MODEL_ASSIGN_RE.finditer(text or ""):
        mid = (m.group(1) or m.group(2) or "").strip().rstrip("',\"}")
        if not mid:
            continue
        if mid.lower() in {"string", "model-id", "<model>"} or mid.startswith("<"):
            continue
        if mid not in found:
            found.append(mid)
    # JSON 数组 "models": ["a","b"]
    for m in re.finditer(r"[\"']models[\"']\s*:\s*\[([^\]]+)\]", text or "", re.I):
        for part in re.findall(r"[\"']([^\"']+)[\"']", m.group(1)):
            if part not in found:
                found.append(part)
    return found[:50]


def _try_json_blob(text: str) -> dict[str, Any]:
    """尝试从粘贴文本中捞出第一个 JSON 对象。"""
    text = (text or "").strip()
    if not text:
        return {}
    # 整段就是 JSON
    for candidate in (text,):
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, TypeError):
            pass
    starts = [i for i, ch in enumerate(text) if ch == "{"]
    for start in starts[:8]:
        try:
            data, _ = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            continue
    return {}


def parse_provider_snippet(text: str) -> dict[str, Any]:
    """解析粘贴内容，返回结构化接入草稿。"""
    raw = text or ""
    warnings: list[str] = []
    evidence: list[str] = []

    blob = _try_json_blob(raw)
    urls = [_normalize_base(u) for u in _URL_RE.findall(raw)]
    urls = [u for u in urls if u]
    # JSON 字段优先
    for key in ("base_url", "api_base", "openai_api_base", "endpoint", "url"):
        if isinstance(blob.get(key), str):
            nb = _normalize_base(blob[key])
            if nb:
                urls.insert(0, nb)
                evidence.append(f"JSON 字段 {key}")
    # 去重保序
    seen_u: set[str] = set()
    uniq_urls: list[str] = []
    for u in urls:
        if u not in seen_u:
            seen_u.add(u)
            uniq_urls.append(u)
    uniq_urls.sort(key=_score_url, reverse=True)

    keys = _extract_keys(raw)
    for key in ("api_key", "openai_api_key", "key", "token", "secret"):
        if isinstance(blob.get(key), str) and len(blob[key]) >= 8:
            if blob[key] not in keys:
                keys.insert(0, blob[key])
                evidence.append(f"JSON 字段 {key}")

    models = _extract_models(raw)
    if isinstance(blob.get("model"), str):
        models.insert(0, blob["model"])
    if isinstance(blob.get("models"), list):
        for item in blob["models"]:
            mid = item.get("id") if isinstance(item, dict) else item
            if isinstance(mid, str) and mid not in models:
                models.append(mid)

    api_base = uniq_urls[0] if uniq_urls else ""
    # 常见：文档只写了 chat 完整路径，已在 normalize 去掉
    if api_base and not api_base.rstrip("/").endswith("/v1"):
        # 若路径为空，提示用户确认；不强制加 /v1（有的站根路径即兼容）
        if urlsplit(api_base).path in {"", "/"}:
            warnings.append("地址看起来像站点根路径；若文档要求 /v1，请在确认时补上")

    api_key = keys[0] if keys else ""
    model = models[0] if models else ""
    provider_name = ""
    if isinstance(blob.get("name"), str):
        provider_name = blob["name"].strip()[:80]
    if not provider_name and isinstance(blob.get("provider"), str):
        provider_name = blob["provider"].strip()[:80]
    if not provider_name:
        provider_name = _guess_provider_name(api_base or None, raw)

    if api_base:
        evidence.append(f"Base URL ← {api_base}")
    if api_key:
        evidence.append("API Key ← Bearer / 赋值 / 环境变量")
    if model:
        evidence.append(f"模型 ← {model}")
    if not api_base:
        warnings.append("未识别到 API 地址，请确认粘贴内容含 https://… 接口")
    if not api_key:
        warnings.append("未识别到 API Key；可继续解析后在下方补填")

    confidence = 0.2
    if api_base:
        confidence += 0.35
    if api_key:
        confidence += 0.3
    if model:
        confidence += 0.1
    if evidence:
        confidence += 0.05
    confidence = min(0.99, confidence)

    return {
        "provider_name": provider_name,
        "api_base": api_base,
        "api_key": api_key,
        "model": model,
        "models_mentioned": models[:20],
        "candidate_bases": uniq_urls[:5],
        "confidence": round(confidence, 2),
        "warnings": warnings,
        "evidence": evidence,
        "raw_chars": len(raw),
    }
