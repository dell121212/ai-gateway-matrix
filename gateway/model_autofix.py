#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
免费模型改名自愈
————————————————————————————————————————
场景：智谱/OpenRouter 等免费模型 id 常小改几个字母，不会整表一起变。
当请求因「模型不存在」失败，或目录审计标 model_missing 时：

  1. 拉上游 /models 目录
  2. 用字符串相似度筛出候选（通常只变几个字母）
  3. 可选：调强模型从候选里裁决
  4. 置信度够高则写回 config.yaml 并热加载 Router

安全：
  · 同一渠道有冷却，避免刷屏改配置
  · 仅在候选与旧名足够像时自动改；否则只记建议不改
  · 保留 LiteLLM provider 前缀（openrouter/、openai/…）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("ai_gateway_matrix.model_autofix")

_ROOT = Path(__file__).resolve().parent.parent
_STATE_PATH = Path(
    os.environ.get("MODEL_AUTOFIX_STATE", str(_ROOT / "state" / "model-autofix.json"))
)
_CONFIG_PATH = Path(os.environ.get("SOURCE_GATEWAY_CONFIG_PATH", str(_ROOT / "config.yaml")))

# 同一渠道两次自动改名的最短间隔（秒）
_COOLDOWN_SEC = int(os.environ.get("MODEL_AUTOFIX_COOLDOWN_SEC", "900") or "900")
_MIN_SIMILARITY = float(os.environ.get("MODEL_AUTOFIX_MIN_SIMILARITY", "0.55") or "0.55")
_MIN_AUTO_CONFIDENCE = float(os.environ.get("MODEL_AUTOFIX_MIN_CONFIDENCE", "0.72") or "0.72")
_MAX_CANDIDATES = 12
_ENABLE = (os.environ.get("MODEL_AUTOFIX_ENABLE", "true") or "true").lower() not in {
    "0",
    "false",
    "no",
    "off",
}

_lock = threading.RLock()
_recent: dict[str, float] = {}  # fix_key -> last_attempt_ts

# LiteLLM 第一段 provider（与 config_editor 对齐的精简集）
_LITELLM_PREFIXES = frozenset(
    {
        "openai",
        "openrouter",
        "gemini",
        "groq",
        "cerebras",
        "sambanova",
        "mistral",
        "deepseek",
        "together_ai",
        "together",
        "fireworks_ai",
        "fireworks",
        "huggingface",
        "deepinfra",
        "novita",
        "moonshot",
        "dashscope",
        "nvidia_nim",
    }
)

_MODEL_MISSING_MARKERS = (
    "model_not_found",
    "model not found",
    "does not exist",
    "invalid model",
    "unknown model",
    "no such model",
    "not a valid model",
    "model_not_available",
    "不存在",
    "无此模型",
    "模型不存在",
    "invalid_request_error",  # 常与 model 字段一起出现，需结合 model 关键词
)


def is_model_name_error(error_text: str) -> bool:
    t = (error_text or "").lower()
    if not t:
        return False
    if any(m in t for m in _MODEL_MISSING_MARKERS if m != "invalid_request_error"):
        return True
    if "invalid_request_error" in t and "model" in t:
        return True
    if "model" in t and any(x in t for x in ("not found", "unknown", "invalid", "不存在")):
        return True
    return False


def split_litellm_model(model: str) -> tuple[str, str]:
    """返回 (provider_prefix_or_empty, upstream_id)。"""
    model = (model or "").strip()
    if "/" not in model:
        return "", model
    head, rest = model.split("/", 1)
    if head in _LITELLM_PREFIXES:
        return head, rest
    return "", model


def join_litellm_model(prefix: str, upstream_id: str) -> str:
    upstream_id = (upstream_id or "").strip()
    if prefix:
        return f"{prefix}/{upstream_id}"
    return upstream_id


def similarity(a: str, b: str) -> float:
    a, b = (a or "").lower(), (b or "").lower()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # 去版本号/tag 噪音后的比分
    base = SequenceMatcher(None, a, b).ratio()
    a0, b0 = a.split(":")[0], b.split(":")[0]
    base = max(base, SequenceMatcher(None, a0, b0).ratio())
    # 公共前缀小幅加分，但不得抬成满分（满分仅完全相等）
    common = 0
    for x, y in zip(a0, b0):
        if x == y:
            common += 1
        else:
            break
    prefix_bonus = min(0.06, common / max(len(a0), len(b0), 1) * 0.12)
    return min(0.99, base + prefix_bonus)


def rank_candidates(old_upstream: str, catalog: list[str], limit: int = _MAX_CANDIDATES) -> list[tuple[str, float]]:
    scored: list[tuple[str, float]] = []
    for item in catalog:
        if not item or item == old_upstream:
            continue
        if not _preserves_model_contract(old_upstream, item):
            continue
        s = similarity(old_upstream, item)
        if s < _MIN_SIMILARITY:
            # 仍允许同厂商路径前缀匹配：meta-llama/xxx vs meta-llama/yyy
            if "/" in old_upstream and "/" in item:
                if old_upstream.rsplit("/", 1)[0] == item.rsplit("/", 1)[0] and s >= 0.4:
                    scored.append((item, s))
            continue
        scored.append((item, s))
    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored[:limit]


def _preserves_model_contract(old: str, candidate: str) -> bool:
    """Reject fuzzy matches that silently change cost, modality, family or size.

    String similarity alone considers ``Qwen2.5-7B-Instruct`` very close to a
    Qwen-VL model and considers removing OpenRouter's ``:free`` suffix an almost
    perfect match.  Both are unsafe automatic edits.
    """
    old_l = (old or "").lower()
    new_l = (candidate or "").lower()
    if old_l.endswith(":free") and not new_l.endswith(":free"):
        return False

    old_vendor = old_l.split("/", 1)[0] if "/" in old_l else ""
    new_vendor = new_l.split("/", 1)[0] if "/" in new_l else ""
    if old_vendor and new_vendor and old_vendor != new_vendor:
        return False

    def tokens(value: str) -> set[str]:
        return {x for x in re.split(r"[^a-z0-9.]+", value) if x}

    old_tokens, new_tokens = tokens(old_l), tokens(new_l)
    modality_tokens = {"vl", "vision", "image", "audio", "tts", "embedding", "reranker", "ocr"}
    if (new_tokens & modality_tokens) - old_tokens:
        return False
    for qualifier in ("flash", "mini", "nano", "small", "large"):
        if qualifier in old_tokens and qualifier not in new_tokens:
            return False

    size_re = re.compile(r"(?<![a-z0-9])(\d+(?:\.\d+)?)b(?![a-z0-9])")
    old_sizes = [float(x) for x in size_re.findall(old_l)]
    new_sizes = [float(x) for x in size_re.findall(new_l)]
    if old_sizes and new_sizes:
        ratio = max(old_sizes[0], new_sizes[0]) / max(min(old_sizes[0], new_sizes[0]), 0.1)
        if ratio > 1.5:
            return False
    return True


def fetch_model_catalog(api_base: str, api_key: str, *, google_style: bool = False) -> list[str]:
    base = (api_base or "").rstrip("/")
    if not base or not api_key:
        return []
    url = base + "/models"
    headers = {"Accept": "application/json"}
    if google_style:
        headers["x-goog-api-key"] = api_key
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    req = Request(url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        logger.info("[model-autofix] 拉取目录失败 %s: %s", url, type(exc).__name__)
        return []
    if isinstance(payload, dict):
        entries = payload.get("data") or payload.get("models") or []
    elif isinstance(payload, list):
        entries = payload
    else:
        entries = []
    ids: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        identifier = entry.get("id") or entry.get("name")
        if not identifier:
            continue
        identifier = str(identifier)
        if identifier.startswith("models/"):
            identifier = identifier.split("/", 1)[1]
        ids.append(identifier)
    return ids


async def _llm_pick(
    old_upstream: str,
    candidates: list[tuple[str, float]],
    provider_hint: str = "",
) -> Optional[tuple[str, float]]:
    """用强模型在候选里裁决；失败返回 None。"""
    try:
        from gateway.llm_classifier import resolve_classifier_backend
        import litellm
    except Exception:
        return None
    backend = resolve_classifier_backend()
    if not backend:
        return None
    lines = "\n".join(f"- {c} (sim={s:.2f})" for c, s in candidates[:10])
    prompt = (
        "免费 API 的模型 id 常发生小改名（几个字母/版本号变化），不会整表一起变。\n"
        f"旧模型 id：{old_upstream}\n"
        f"供应商提示：{provider_hint or 'unknown'}\n"
        f"上游 /models 中最接近的候选：\n{lines}\n\n"
        "请判断是否存在「明显是同一模型的改名/替换」。\n"
        "只输出 JSON，不要其它文字：\n"
        '{"pick":"<候选中的完整 id 或 null>","confidence":0.0到1.0,"reason":"一句话"}\n'
        "规则：只有高度像改名时才 pick；拿不准就 pick=null。"
    )
    kwargs: dict[str, Any] = {
        "model": backend["model"],
        "api_key": backend["api_key"],
        "messages": [
            {"role": "system", "content": "你是模型目录对齐助手，只输出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 120,
        "temperature": 0,
        "timeout": 12,
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
        pick = data.get("pick")
        conf = float(data.get("confidence") or 0)
        if not pick or pick is None or str(pick).lower() == "null":
            return None
        pick = str(pick).strip()
        allowed = {c for c, _ in candidates}
        if pick not in allowed:
            # 模型可能丢了前缀或加了前缀
            for c in allowed:
                if c.endswith(pick) or pick.endswith(c) or c.split("/")[-1] == pick.split("/")[-1]:
                    pick = c
                    break
            else:
                return None
        return pick, conf
    except Exception as exc:
        logger.info("[model-autofix] 强模型裁决失败: %s", type(exc).__name__)
        return None


def _load_state() -> dict[str, Any]:
    try:
        if _STATE_PATH.is_file():
            return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return {"fixes": [], "suggestions": []}


def _save_state(state: dict[str, Any]) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(_STATE_PATH)
    except OSError as exc:
        logger.warning("[model-autofix] 写状态失败: %s", exc)


def _infer_api_base(model: str, api_base: Optional[str]) -> tuple[Optional[str], bool]:
    if api_base:
        return api_base.rstrip("/"), "generativelanguage.googleapis" in api_base
    prefix, _ = split_litellm_model(model)
    table = {
        "openrouter": ("https://openrouter.ai/api/v1", False),
        "groq": ("https://api.groq.com/openai/v1", False),
        "cerebras": ("https://api.cerebras.ai/v1", False),
        "sambanova": ("https://api.sambanova.ai/v1", False),
        "mistral": ("https://api.mistral.ai/v1", False),
        "deepseek": ("https://api.deepseek.com/v1", False),
        "together_ai": ("https://api.together.xyz/v1", False),
        "together": ("https://api.together.xyz/v1", False),
        "deepinfra": ("https://api.deepinfra.com/v1/openai", False),
        "gemini": ("https://generativelanguage.googleapis.com/v1beta", True),
        "openai": (None, False),  # 需 api_base（智谱等）
    }
    hit = table.get(prefix)
    if hit:
        return hit
    return None, False


async def resolve_replacement(
    *,
    old_model: str,
    api_base: Optional[str],
    api_key: str,
    use_llm: bool = True,
) -> Optional[dict[str, Any]]:
    """返回 {new_model, old_model, confidence, method, candidates} 或 None。"""
    if not _ENABLE or not api_key or not old_model:
        return None
    prefix, old_up = split_litellm_model(old_model)
    base, google = _infer_api_base(old_model, api_base)
    # openai/ 自定义端点（智谱）必须有 api_base
    if not base and prefix == "openai" and api_base:
        base = api_base.rstrip("/")
    if not base:
        logger.info("[model-autofix] 无法解析目录 URL，跳过 %s", old_model)
        return None

    catalog = await asyncio.to_thread(fetch_model_catalog, base, api_key, google_style=google)
    if not catalog:
        return None
    # 已在目录中则无需改
    if old_up in catalog or old_model in catalog:
        return None

    ranked = rank_candidates(old_up, catalog)
    if not ranked:
        return None

    method = "fuzzy"
    confidence = ranked[0][1]
    pick_up = ranked[0][0]

    if use_llm and len(ranked) >= 1:
        llm = await _llm_pick(old_up, ranked, provider_hint=prefix or base)
        if llm:
            pick_up, conf = llm
            # 综合相似度与 LLM 置信度
            sim = next((s for c, s in ranked if c == pick_up), 0.5)
            confidence = max(sim, min(1.0, conf))
            method = "llm+fuzzy"

    if confidence < _MIN_AUTO_CONFIDENCE:
        return {
            "new_model": None,
            "old_model": old_model,
            "confidence": confidence,
            "method": method,
            "candidates": [{"id": c, "score": s} for c, s in ranked[:5]],
            "auto_applied": False,
            "reason": "confidence_too_low",
        }

    new_model = join_litellm_model(prefix, pick_up)
    if new_model == old_model:
        return None
    return {
        "new_model": new_model,
        "old_model": old_model,
        "confidence": confidence,
        "method": method,
        "candidates": [{"id": c, "score": s} for c, s in ranked[:5]],
        "auto_applied": False,
    }


def apply_model_rename(
    *,
    pool: str,
    old_model: str,
    new_model: str,
    api_base: Optional[str],
    env_var: Optional[str],
) -> bool:
    from dashboard.config_editor import update_model

    try:
        ok = update_model(
            _CONFIG_PATH,
            pool=pool,
            model=old_model,
            api_base=api_base,
            env_var=env_var,
            new_model=new_model,
        )
        if ok:
            from gateway import priority_overrides

            priority_overrides.rename_model(
                pool, old_model, new_model, api_base, env_var
            )
        return bool(ok)
    except Exception as exc:
        logger.warning("[model-autofix] 写 config 失败: %s", exc)
        return False


async def maybe_autofix_from_failure(kwargs: dict) -> Optional[dict[str, Any]]:
    """请求失败回调入口：疑似模型名错误时尝试自愈。"""
    if not _ENABLE:
        return None
    err = str(kwargs.get("exception") or "")
    if not is_model_name_error(err):
        return None

    litellm_params = kwargs.get("litellm_params") or {}
    old_model = litellm_params.get("model") or ""
    api_base = litellm_params.get("api_base")
    api_key = litellm_params.get("api_key") or ""
    # 有时 key 在 metadata
    if not api_key:
        meta = kwargs.get("litellm_params", {}).get("metadata") or {}
        api_key = meta.get("api_key") or ""

    # 从 os.environ 解析 os.environ/XXX 不会出现在 kwargs 里通常已是明文
    if isinstance(api_key, str) and api_key.startswith("os.environ/"):
        api_key = os.environ.get(api_key.split("/", 1)[1], "")

    # 定位 pool + env_var：扫 config
    pool, env_var, cfg_base = _lookup_deployment(old_model, api_base)
    if not pool:
        # 尝试用完整 litellm model 字符串在 config 里找
        return None
    if cfg_base is not None:
        api_base = cfg_base

    fix_key = f"{env_var or ''}::{old_model}"
    now = time.time()
    with _lock:
        last = _recent.get(fix_key, 0)
        if now - last < _COOLDOWN_SEC:
            return None
        _recent[fix_key] = now

    result = await resolve_replacement(
        old_model=old_model if "/" in old_model else _guess_full_model(old_model, env_var),
        api_base=api_base,
        api_key=api_key or (os.environ.get(env_var or "", "") if env_var else ""),
        use_llm=True,
    )
    if not result:
        return None

    state = _load_state()
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pool": pool,
        "env_var": env_var,
        **result,
    }

    if result.get("new_model"):
        full_old = result["old_model"]
        # resolve 可能用了补全后的 old_model
        applied = apply_model_rename(
            pool=pool,
            old_model=_config_model_string(pool, env_var, api_base) or full_old,
            new_model=result["new_model"],
            api_base=api_base,
            env_var=env_var,
        )
        entry["auto_applied"] = applied
        if applied:
            logger.warning(
                "[model-autofix] 已自动改名 %s → %s（confidence=%.2f, method=%s）",
                full_old,
                result["new_model"],
                result.get("confidence") or 0,
                result.get("method"),
            )
            try:
                from gateway import env_sync
                env_sync.reload_runtime_router(force=True)
            except Exception as exc:
                logger.info("[model-autofix] 热加载提示: %s", exc)
            state.setdefault("fixes", []).append(entry)
            state["fixes"] = state["fixes"][-50:]
        else:
            state.setdefault("suggestions", []).append(entry)
            state["suggestions"] = state["suggestions"][-50:]
    else:
        state.setdefault("suggestions", []).append(entry)
        state["suggestions"] = state["suggestions"][-50:]
        logger.info(
            "[model-autofix] 仅建议未自动应用 old=%s candidates=%s",
            result.get("old_model"),
            result.get("candidates"),
        )

    _save_state(state)
    return entry


def _config_model_string(pool: str, env_var: Optional[str], api_base: Optional[str]) -> Optional[str]:
    try:
        import yaml
        cfg = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
        for item in cfg.get("model_list") or []:
            if item.get("model_name") != pool:
                continue
            p = item.get("litellm_params") or {}
            key = p.get("api_key") or ""
            e = key.split("/", 1)[1] if isinstance(key, str) and key.startswith("os.environ/") else None
            if e != env_var:
                continue
            if (p.get("api_base") or None) != (api_base or None):
                # 允许 base 规范化差异
                if (p.get("api_base") or "").rstrip("/") != (api_base or "").rstrip("/"):
                    continue
            return p.get("model")
    except Exception:
        return None
    return None


def _lookup_deployment(model: str, api_base: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """(pool, env_var, api_base_from_config)。"""
    try:
        import yaml
        cfg = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None, None, None
    prefix, up = split_litellm_model(model)
    for item in cfg.get("model_list") or []:
        pool = item.get("model_name")
        if pool not in {"fast-pool", "free-pool", "strong-model-pool", "elite-model-pool"}:
            continue
        p = item.get("litellm_params") or {}
        m = p.get("model") or ""
        if m == model or m.endswith("/" + model) or split_litellm_model(m)[1] == up or m == up:
            key = p.get("api_key") or ""
            e = key.split("/", 1)[1] if isinstance(key, str) and key.startswith("os.environ/") else None
            return pool, e, p.get("api_base")
    return None, None, None


def _guess_full_model(short: str, env_var: Optional[str]) -> str:
    try:
        import yaml
        cfg = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
        for item in cfg.get("model_list") or []:
            p = item.get("litellm_params") or {}
            m = p.get("model") or ""
            if short in m:
                return m
    except Exception:
        pass
    return short


async def autofix_missing_from_discovery(results: dict[str, dict]) -> list[dict]:
    """目录审计后：对 model_missing 尝试自动改名。"""
    if not _ENABLE:
        return []
    applied: list[dict] = []
    try:
        import yaml
        from gateway import channel_ids
        cfg = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []

    # display_id -> deployment
    by_id: dict[str, dict] = {}
    for item in cfg.get("model_list") or []:
        pool = item.get("model_name")
        if pool not in {"fast-pool", "free-pool", "strong-model-pool", "elite-model-pool"}:
            continue
        p = item.get("litellm_params") or {}
        model = p.get("model") or ""
        api_base = p.get("api_base")
        key = p.get("api_key") or ""
        env_var = key.split("/", 1)[1] if isinstance(key, str) and key.startswith("os.environ/") else None
        did = channel_ids.make_display_id(model, api_base, env_var)
        legacy = channel_ids.make_legacy_display_id(model, api_base, env_var)
        by_id[did] = {"pool": pool, "model": model, "api_base": api_base, "env_var": env_var}
        by_id[legacy] = by_id[did]

    for display_id, info in (results or {}).items():
        if (info or {}).get("status") != "model_missing":
            continue
        dep = by_id.get(display_id)
        if not dep:
            continue
        env_var = dep["env_var"]
        api_key = os.environ.get(env_var or "", "").strip()
        if not api_key:
            continue
        fix_key = f"{env_var}::{dep['model']}"
        now = time.time()
        with _lock:
            if now - _recent.get(fix_key, 0) < _COOLDOWN_SEC:
                continue
            _recent[fix_key] = now
        result = await resolve_replacement(
            old_model=dep["model"],
            api_base=dep.get("api_base"),
            api_key=api_key,
            use_llm=True,
        )
        if not result or not result.get("new_model"):
            continue
        ok = apply_model_rename(
            pool=dep["pool"],
            old_model=dep["model"],
            new_model=result["new_model"],
            api_base=dep.get("api_base"),
            env_var=env_var,
        )
        result["auto_applied"] = ok
        result["pool"] = dep["pool"]
        result["env_var"] = env_var
        if ok:
            applied.append(result)
            logger.warning(
                "[model-autofix] 目录审计触发改名 %s → %s",
                dep["model"],
                result["new_model"],
            )
    if applied:
        state = _load_state()
        for a in applied:
            state.setdefault("fixes", []).append(
                {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **a}
            )
        state["fixes"] = state["fixes"][-50:]
        _save_state(state)
        try:
            from gateway import env_sync
            env_sync.reload_runtime_router(force=True)
        except Exception:
            pass
    return applied
