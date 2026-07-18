#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config.yaml 编辑工具

允许从仪表盘修改渠道优先级和上游模型名称。

设计取舍：
  · 直接改 config.yaml 文本；改完后网关需重载/重启才生效。
  · 模型名称分两层：
      - 厂商真实 model id（用户填写，如 gemini-2.5-pro、minimax-m2.7）
      - LiteLLM 路由串（写入 config，如 gemini/gemini-2.5-pro、openai/minimax-m2.7）
    用户不必手写 gemini/ openai/ 等 provider 前缀；保存时按旧配置/api_base 自动补全。
  · 定位：优先 (pool, model, api_base, env_var)；direct-* 若因历史改名哈希漂移，
    会按 YAML 锚点或 env+api_base+model 回退查找，避免「无法唯一定位」假失败。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml
from gateway import channel_ids

from .safe_files import locked_file, safe_rewrite

_MODEL_NAME_SPLIT_RE = re.compile(r"(?=^  - model_name:)", re.M)
_UPSTREAM_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,299}$")

# LiteLLM 用「provider/实际模型」；第一段是路由用 provider，不是厂商 model id 的一部分。
# 见 https://docs.litellm.ai/docs/providers 与 openai_compatible 文档。
LITELLM_PROVIDER_PREFIXES = frozenset(
    {
        "openai",
        "text-completion-openai",
        "azure",
        "azure_ai",
        "gemini",
        "vertex_ai",
        "vertex_ai_beta",
        "groq",
        "cerebras",
        "sambanova",
        "mistral",
        "openrouter",
        "deepseek",
        "together_ai",
        "together",
        "fireworks_ai",
        "fireworks",
        "moonshot",
        "dashscope",
        "huggingface",
        "deepinfra",
        "novita",
        "nvidia_nim",
        "anthropic",
        "cohere",
        "bedrock",
        "replicate",
        "perplexity",
        "ai21",
        "cloudflare",
        "ollama",
        "hosted_vllm",
        "codestral",
        "friendliai",
        "databricks",
        "xai",
    }
)


def strip_litellm_provider(model: str) -> str:
    """去掉 LiteLLM provider 前缀，得到更接近厂商文档的模型 id。"""
    model = (model or "").strip()
    if "/" not in model:
        return model
    head, rest = model.split("/", 1)
    if head in LITELLM_PROVIDER_PREFIXES and rest:
        return rest
    return model


def litellm_provider_of(model: str) -> Optional[str]:
    model = (model or "").strip()
    if "/" not in model:
        return None
    head = model.split("/", 1)[0]
    return head if head in LITELLM_PROVIDER_PREFIXES else None


def infer_litellm_provider(
    *,
    old_model: str,
    api_base: Optional[str],
    env_var: Optional[str],
) -> str:
    """为裸模型名推断应写入 config 的 LiteLLM provider 前缀。"""
    p = litellm_provider_of(old_model)
    if p:
        return p

    base = (api_base or "").lower()
    env = (env_var or "").upper()
    if "generativelanguage.googleapis" in base or env == "GEMINI_API_KEY":
        return "gemini"
    if "api.groq.com" in base or env == "GROQ_API_KEY":
        return "groq"
    if "sambanova" in base or env == "SAMBANOVA_API_KEY":
        return "sambanova"
    if "cerebras" in base or env == "CEREBRAS_API_KEY":
        return "cerebras"
    if "mistral" in base or env.startswith("MISTRAL_"):
        return "mistral"
    if "openrouter" in base or env == "OPENROUTER_API_KEY":
        return "openrouter"
    if "deepseek.com" in base or env == "DEEPSEEK_API_KEY":
        return "deepseek"
    if "together" in base or env == "TOGETHER_API_KEY":
        return "together_ai"
    if "fireworks" in base or env == "FIREWORKS_API_KEY":
        return "fireworks_ai"
    if "huggingface" in base or env in {"HF_TOKEN", "HUGGINGFACE_API_KEY"}:
        return "huggingface"
    if "deepinfra" in base or env == "DEEPINFRA_API_KEY":
        return "deepinfra"
    # 自定义 OpenAI 兼容端点（硅基、智谱、GeneralCompute 等）统一走 openai/
    return "openai"


def normalize_upstream_model(
    new_model: str,
    *,
    old_model: str,
    api_base: Optional[str] = None,
    env_var: Optional[str] = None,
) -> str:
    """把用户填写的模型名规范成 LiteLLM 可路由的 provider/model。"""
    new_model = (new_model or "").strip()
    if not new_model:
        raise ValueError("模型名称不能为空")
    if not _UPSTREAM_MODEL_RE.fullmatch(new_model):
        raise ValueError("模型名称只能包含字母、数字以及 . _ : / @ + ~ -，最长 300 字符")

    # 已带合法 LiteLLM provider 前缀 → 原样使用
    if litellm_provider_of(new_model):
        return new_model

    provider = infer_litellm_provider(
        old_model=old_model, api_base=api_base, env_var=env_var
    )
    # 用户若粘贴了与旧配置相同的全串（无 provider 变化）
    if new_model == old_model:
        return new_model
    return f"{provider}/{new_model}"


def _matching_primary_parts(
    parts: list[str],
    pool: str,
    model: str,
    api_base: Optional[str],
    env_var: Optional[str],
) -> list[int]:
    matches = []
    for i, part in enumerate(parts):
        m_pool = re.match(r"  - model_name:\s*(\S+)", part)
        if not m_pool or m_pool.group(1) != pool:
            continue

        # 允许行尾注释；只取第一个非注释 model 行
        m_model = re.search(r"^\s*model:\s*(\S+)", part, re.M)
        if not m_model or m_model.group(1) != model:
            continue

        m_api_base = re.search(r"^\s*api_base:\s*(\S+)", part, re.M)
        found_api_base = m_api_base.group(1) if m_api_base else None
        if found_api_base != (api_base or None):
            continue

        m_key = re.search(r"^\s*api_key:\s*os\.environ/(\S+)", part, re.M)
        found_env = m_key.group(1) if m_key else None
        if found_env != env_var:
            continue
        matches.append(i)
    return matches


def _anchor_name(part: str) -> Optional[str]:
    m = re.search(r"litellm_params:\s*&(\S+)", part)
    return m.group(1) if m else None


def _find_direct_indices(
    parts: list[str],
    *,
    model: str,
    api_base: Optional[str],
    env_var: Optional[str],
    primary_part: Optional[str] = None,
) -> list[int]:
    """定位 direct-* 块。优先按哈希名；失败则锚点/参数回退（修哈希漂移）。"""
    expected = channel_ids.make_direct_model_name(model, api_base, env_var)
    by_name = [
        i
        for i, part in enumerate(parts)
        if re.match(rf"  - model_name:\s*{re.escape(expected)}(?:\s|$|#)", part)
    ]
    if len(by_name) == 1:
        return by_name

    # YAML 锚点：主条目 litellm_params: &foo → direct 为 litellm_params: *foo
    anchor = _anchor_name(primary_part or "")
    if anchor:
        by_anchor = []
        for i, part in enumerate(parts):
            if not re.match(r"  - model_name:\s*direct-\S+", part):
                continue
            if re.search(rf"litellm_params:\s*\*{re.escape(anchor)}\b", part):
                by_anchor.append(i)
        if len(by_anchor) == 1:
            return by_anchor

    # 无锚点完整副本：按 model + api_base + env 匹配
    by_params: list[int] = []
    for i, part in enumerate(parts):
        if not re.match(r"  - model_name:\s*direct-\S+", part):
            continue
        m_model = re.search(r"^\s*model:\s*(\S+)", part, re.M)
        m_key = re.search(r"^\s*api_key:\s*os\.environ/(\S+)", part, re.M)
        m_base = re.search(r"^\s*api_base:\s*(\S+)", part, re.M)
        if not m_model or m_model.group(1) != model:
            continue
        if (m_key.group(1) if m_key else None) != env_var:
            continue
        if (m_base.group(1) if m_base else None) != (api_base or None):
            continue
        by_params.append(i)
    if len(by_params) == 1:
        return by_params

    # 最后手段：注释里带旧 model 名的 direct 行（仅当 env 能从主条目锚点唯一关联时）
    if env_var and model:
        fuzzy = []
        for i, part in enumerate(parts):
            head = part.split("\n", 1)[0]
            if not re.match(r"  - model_name:\s*direct-\S+", head):
                continue
            if model in head or (env_var and env_var in part):
                # 避免同 env 多模型误伤：要求注释/正文出现当前 model 片段
                if model in part:
                    fuzzy.append(i)
        if len(fuzzy) == 1:
            return fuzzy

    return by_name if by_name else (by_params or [])


def allocate_next_env_var(source_env: str, existing: set[str]) -> str:
    """为同一公司分配下一个账号环境变量名。

    · MISTRAL_KEY_1 + 已有 _2 → MISTRAL_KEY_3
    · GROQ_API_KEY → GROQ_API_KEY_2
    """
    if not source_env or not re.fullmatch(r"[A-Z][A-Z0-9_]*", source_env):
        raise ValueError("环境变量名不合法")
    m = re.match(r"^(.*)_(\d+)$", source_env)
    if m:
        base = m.group(1)
        n = int(m.group(2)) + 1
    else:
        base = source_env
        n = 2
    while True:
        candidate = f"{base}_{n}"
        if candidate not in existing:
            return candidate
        n += 1
        if n > 99:
            raise ValueError("账号数量过多（上限 99）")


def add_company_account(config_path: Path, source_env: str) -> dict:
    """克隆某账号的全部主池 deployment 到新 env，用于「添加账号」。

    返回 {new_env_var, cloned_models: int}。新账号 Key 需用户自行填写。
    """
    with locked_file(config_path):
        return _add_company_account_locked(config_path, source_env)


def _add_company_account_locked(config_path: Path, source_env: str) -> dict:
    text = config_path.read_text(encoding="utf-8")
    parts = _MODEL_NAME_SPLIT_RE.split(text)

    # 收集已有 env
    existing: set[str] = set()
    for part in parts:
        for m in re.finditer(r"os\.environ/([A-Z][A-Z0-9_]*)", part):
            existing.add(m.group(1))
    if source_env not in existing:
        raise ValueError(f"config 中不存在 {source_env}")

    new_env = allocate_next_env_var(source_env, existing)

    # 主池条目：含 source_env 的 primary 块
    primary_indices = []
    for i, part in enumerate(parts):
        m_pool = re.match(r"  - model_name:\s*(\S+)", part)
        if not m_pool or m_pool.group(1) not in {
            "fast-pool",
            "free-pool",
            "strong-model-pool",
            "elite-model-pool",
        }:
            continue
        if re.search(rf"api_key:\s*os\.environ/{re.escape(source_env)}\b", part):
            primary_indices.append(i)

    if not primary_indices:
        raise ValueError("该账号没有可克隆的主渠道条目")

    new_blocks: list[str] = []
    suffix = re.sub(r"[^a-z0-9]+", "", new_env.lower())[-12:] or "acct"

    for idx in primary_indices:
        src = parts[idx]
        m_model = re.search(r"^\s*model:\s*(\S+)", src, re.M)
        m_base = re.search(r"^\s*api_base:\s*(\S+)", src, re.M)
        if not m_model:
            continue
        model = m_model.group(1)
        api_base = m_base.group(1) if m_base else None

        block = src
        # 重命名 YAML 锚点，避免与原账号冲突
        anchor = _anchor_name(block)
        new_anchor = None
        if anchor:
            new_anchor = f"{anchor}_{suffix}"
            block = re.sub(
                rf"(litellm_params:\s*)&{re.escape(anchor)}\b",
                rf"\1&{new_anchor}",
                block,
                count=1,
            )
        block = re.sub(
            rf"(api_key:\s*os\.environ/){re.escape(source_env)}\b",
            rf"\1{new_env}",
            block,
        )
        # 确保块以换行结束
        if not block.endswith("\n"):
            block += "\n"
        new_blocks.append(block)

        new_direct = channel_ids.make_direct_model_name(model, api_base, new_env)
        comment = f"{model}" + (f" @ {api_base}" if api_base else "")
        if new_anchor:
            direct_block = (
                f"  - model_name: {new_direct}  # {comment}\n"
                f"    litellm_params: *{new_anchor}\n"
            )
        else:
            # 无锚点：复制完整参数并把 env 换成新账号
            # 从主块提取 litellm_params 段（去掉 model_name 行）
            body = re.sub(r"^  - model_name:.*\n", "", block)
            # body 以 litellm_params: 开头
            direct_block = f"  - model_name: {new_direct}  # {comment}\n" + body
            if not direct_block.endswith("\n"):
                direct_block += "\n"
        new_blocks.append(direct_block)

    # 插在最后一个 model_list 条目之后（即 parts 末尾 model 块后、router 段前）
    # parts[0] 是 header；其余是 model_name 段。router_settings 在某个 part 之后的尾部。
    # 找最后一个以 "  - model_name:" 开头的 part，在其后插入。
    insert_at = len(parts)
    for i in range(len(parts) - 1, 0, -1):
        if re.match(r"  - model_name:", parts[i]):
            insert_at = i + 1
            break

    # 若最后一个 part 尾部含 router_settings，需拆开
    if insert_at <= len(parts) - 1:
        pass
    # 检查 parts[-1] 是否包含 router_settings（与最后一个 model 粘在一起的情况）
    # split 用 lookahead，每个 part 从 model_name 开始直到下一个 model_name；
    # 最后一个 part 会一直到文件末尾，含 router_settings。
    last_idx = len(parts) - 1
    last = parts[last_idx]
    m_router = re.search(r"\n(?=# ═+\n#  Router|# ════════════════════════════════|router_settings:)", last)
    if m_router and re.match(r"  - model_name:", last):
        model_part = last[: m_router.start() + 1]
        tail = last[m_router.start() + 1 :]
        parts[last_idx] = model_part
        # 追加克隆块 + tail
        for b in new_blocks:
            parts.append(b if b.endswith("\n") else b + "\n")
        parts.append(tail if tail.startswith("\n") or tail.startswith("#") else "\n" + tail)
    else:
        for b in reversed(new_blocks):
            parts.insert(insert_at, b if b.endswith("\n") else b + "\n")

    safe_rewrite(
        config_path,
        "".join(parts),
        mode=0o640,
        validator=_validate_yaml,
    )
    return {
        "new_env_var": new_env,
        "cloned_models": len(primary_indices),
        "source_env_var": source_env,
    }


def update_priority(
    config_path: Path,
    pool: str,
    model: str,
    api_base: Optional[str],
    env_var: Optional[str],
    new_priority: int,
) -> bool:
    """把指定渠道在 config.yaml 里的 priority 字段改成 new_priority。"""
    with locked_file(config_path):
        return _update_priority_locked(
            config_path, pool, model, api_base, env_var, new_priority
        )


def update_model(
    config_path: Path,
    pool: str,
    model: str,
    api_base: Optional[str],
    env_var: Optional[str],
    new_model: str,
) -> bool:
    """修改一个主 deployment 的上游模型名，并同步它的 direct 分组。"""
    new_model = normalize_upstream_model(
        new_model, old_model=model, api_base=api_base, env_var=env_var
    )
    if new_model == model:
        return True

    with locked_file(config_path):
        text = config_path.read_text(encoding="utf-8")
        parts = _MODEL_NAME_SPLIT_RE.split(text)
        matches = _matching_primary_parts(parts, pool, model, api_base, env_var)
        if len(matches) != 1:
            return False

        primary_idx = matches[0]
        primary_part = parts[primary_idx]
        direct_matches = _find_direct_indices(
            parts,
            model=model,
            api_base=api_base,
            env_var=env_var,
            primary_part=primary_part,
        )
        if len(direct_matches) != 1:
            return False

        old_direct = channel_ids.make_direct_model_name(model, api_base, env_var)
        # 实际块上的名字可能已漂移，以匹配到的块为准
        m_existing = re.match(
            r"  - model_name:\s*(direct-\S+)", parts[direct_matches[0]]
        )
        old_direct_actual = m_existing.group(1) if m_existing else old_direct
        new_direct = channel_ids.make_direct_model_name(new_model, api_base, env_var)

        if any(
            i != direct_matches[0]
            and re.match(rf"  - model_name:\s*{re.escape(new_direct)}(?:\s|$|#)", part)
            for i, part in enumerate(parts)
        ):
            raise ValueError("修改后的模型与现有渠道生成了重复的直连标识")

        parts[primary_idx] = re.sub(
            rf"(^\s*model:\s*){re.escape(model)}(\s*(?:#.*)?)?$",
            rf"\g<1>{new_model}\2",
            parts[primary_idx],
            count=1,
            flags=re.M,
        )

        direct_idx = direct_matches[0]
        direct_block = re.sub(
            rf"(^  - model_name:\s*){re.escape(old_direct_actual)}(?=\s|$|#)",
            rf"\g<1>{new_direct}",
            parts[direct_idx],
            count=1,
            flags=re.M,
        )
        # 无锚点 direct 块包含完整参数副本，需要同步实际 model；有锚点时
        # 参数会自动继承主条目，只更新 direct 分组名即可。
        if "litellm_params: *" not in direct_block:
            direct_block = re.sub(
                rf"(^\s*model:\s*){re.escape(model)}(\s*(?:#.*)?)?$",
                rf"\g<1>{new_model}\2",
                direct_block,
                count=1,
                flags=re.M,
            )
        # 同步行尾说明；不参与 YAML 语义。
        if f"# {model}" in direct_block:
            direct_block = direct_block.replace(f"# {model}", f"# {new_model}", 1)
        else:
            # 注释可能是去前缀后的短名
            short_old = strip_litellm_provider(model)
            short_new = strip_litellm_provider(new_model)
            if short_old and f"# {short_old}" in direct_block:
                direct_block = direct_block.replace(f"# {short_old}", f"# {short_new}", 1)
        parts[direct_idx] = direct_block

        safe_rewrite(
            config_path,
            "".join(parts),
            mode=0o640,
            validator=_validate_yaml,
        )
        return True


def _validate_yaml(path: Path) -> None:
    with path.open(encoding="utf-8") as f:
        parsed = yaml.safe_load(f)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("model_list"), list):
        raise ValueError("config.yaml 缺少 model_list")
    direct = {
        item.get("model_name"): item.get("litellm_params") or {}
        for item in parsed["model_list"]
        if str(item.get("model_name", "")).startswith("direct-")
    }
    for item in parsed["model_list"]:
        if item.get("model_name") not in {
            "fast-pool",
            "free-pool",
            "strong-model-pool",
            "elite-model-pool",
        }:
            continue
        params = item.get("litellm_params") or {}
        key_ref = params.get("api_key", "")
        env_var = (
            key_ref.split("/", 1)[1]
            if isinstance(key_ref, str) and key_ref.startswith("os.environ/")
            else None
        )
        expected = channel_ids.make_direct_model_name(
            params.get("model", ""), params.get("api_base"), env_var
        )
        if expected not in direct:
            raise ValueError(f"{expected} 与主 deployment 缺少对应 direct 分组")
        if direct.get(expected) != params:
            raise ValueError(f"{expected} 与主 deployment 参数漂移")


def _update_priority_locked(
    config_path: Path,
    pool: str,
    model: str,
    api_base: Optional[str],
    env_var: Optional[str],
    new_priority: int,
) -> bool:
    text = config_path.read_text(encoding="utf-8")
    parts = _MODEL_NAME_SPLIT_RE.split(text)

    matches = _matching_primary_parts(parts, pool, model, api_base, env_var)

    if len(matches) != 1:
        return False

    idx = matches[0]
    block = parts[idx]
    # 允许行尾注释：priority: 90  # xxx
    if re.search(r"^\s*priority:\s*-?\d+\s*(?:#.*)?$", block, re.M):
        new_block = re.sub(
            r"(^\s*priority:\s*)-?\d+(\s*(?:#.*)?)?$",
            rf"\g<1>{new_priority}\2",
            block,
            count=1,
            flags=re.M,
        )
    else:
        new_block = block.rstrip("\n") + f"\n      priority: {new_priority}\n"

    parts[idx] = new_block

    direct_matches = _find_direct_indices(
        parts,
        model=model,
        api_base=api_base,
        env_var=env_var,
        primary_part=parts[idx],
    )
    if len(direct_matches) != 1:
        return False
    direct_idx = direct_matches[0]
    direct_block = parts[direct_idx]
    # 锚点复用主条目参数：改主条目即可，无需在 direct 块再写 priority
    if "litellm_params: *" not in direct_block:
        if re.search(r"^\s*priority:\s*-?\d+\s*(?:#.*)?$", direct_block, re.M):
            parts[direct_idx] = re.sub(
                r"(^\s*priority:\s*)-?\d+(\s*(?:#.*)?)?$",
                rf"\g<1>{new_priority}\2",
                direct_block,
                count=1,
                flags=re.M,
            )
        else:
            parts[direct_idx] = (
                direct_block.rstrip("\n") + f"\n      priority: {new_priority}\n"
            )
    safe_rewrite(
        config_path,
        "".join(parts),
        mode=0o640,
        validator=_validate_yaml,
    )
    return True
