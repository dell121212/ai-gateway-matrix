#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config.yaml 编辑工具 (v1)
————————————————————————————————————————
按你的要求：允许从仪表盘修改渠道优先级和上游模型名称。

设计取舍：
  · 跟改 API Key 一样的哲学——直接改 config.yaml 的文本，需要再次执行
    bash run.sh 才生效，不假装能热更新 LiteLLM Router 内部
    已经加载好的 deployment 优先级（那需要伸手改 Router 的内存状态，
    这类未公开支持的内部实现细节，版本一换就可能出问题，不划算）。
  · 优先级只改主条目和必要的无锚点 direct 副本；模型名称会同步主条目
    与 direct-xxxxxxxxxx 分组名/参数：
      - 如果这个渠道本来就有 YAML 锚点（官方直营渠道基本都有），
        主条目就是锚点定义本身，改了这里，trusted-pool/direct- 里用
        `*anchor_name` 引用它的地方会在下次解析时自动跟着变，不用
        额外修改参数副本，但 direct 分组名仍会按新模型重新计算。
      - 如果没有锚点（大部分第三方托管/中转站渠道），trusted-pool 里
        本来就没有这个渠道（设计上就不该有），direct- 分组里那份完整
        复制的 priority 字段本身也不影响任何实际路由行为——那个分组
        永远只有一个 deployment，priority 是用来在"多个 deployment
        之间选一个"时才起作用的字段，单一分组里它是个无意义的摆设，
        不同步也没关系。
  · 修改前用 (pool, model, api_base, env_var) 四个字段联合定位，
    必须精确匹配到唯一一条记录才动手改；找不到或者匹配到多条（理论上
    不应该发生，config.yaml 里这四个字段的组合是唯一的）都直接放弃、
    返回 False，不去猜、不去蒙——宁可这次操作失败，也不能改错地方。
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

        m_model = re.search(r"^\s*model:\s*(\S+)\s*$", part, re.M)
        if not m_model or m_model.group(1) != model:
            continue

        m_api_base = re.search(r"^\s*api_base:\s*(\S+)\s*$", part, re.M)
        found_api_base = m_api_base.group(1) if m_api_base else None
        if found_api_base != (api_base or None):
            continue

        m_key = re.search(r"^\s*api_key:\s*os\.environ/(\S+)\s*$", part, re.M)
        found_env = m_key.group(1) if m_key else None
        if found_env != env_var:
            continue
        matches.append(i)
    return matches


def update_priority(
    config_path: Path,
    pool: str,
    model: str,
    api_base: Optional[str],
    env_var: Optional[str],
    new_priority: int,
) -> bool:
    """把指定渠道在 config.yaml 里的 priority 字段改成 new_priority。

    返回 True 表示改成功了（还没重启，改动要重启网关容器才生效）；
    返回 False 表示没找到唯一匹配的条目，config.yaml 没有被改动。
    """
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
    new_model = new_model.strip()
    if not _UPSTREAM_MODEL_RE.fullmatch(new_model):
        raise ValueError("模型名称只能包含字母、数字以及 . _ : / @ + ~ -，最长 300 字符")
    if new_model == model:
        return True

    with locked_file(config_path):
        text = config_path.read_text(encoding="utf-8")
        parts = _MODEL_NAME_SPLIT_RE.split(text)
        matches = _matching_primary_parts(parts, pool, model, api_base, env_var)
        if len(matches) != 1:
            return False

        old_direct = channel_ids.make_direct_model_name(model, api_base, env_var)
        new_direct = channel_ids.make_direct_model_name(new_model, api_base, env_var)
        direct_matches = [
            i for i, part in enumerate(parts)
            if re.match(rf"  - model_name:\s*{re.escape(old_direct)}(?:\s|$)", part)
        ]
        if len(direct_matches) != 1:
            return False
        if any(
            i != direct_matches[0]
            and re.match(rf"  - model_name:\s*{re.escape(new_direct)}(?:\s|$)", part)
            for i, part in enumerate(parts)
        ):
            raise ValueError("修改后的模型与现有渠道生成了重复的直连标识")

        primary_idx = matches[0]
        parts[primary_idx] = re.sub(
            rf"(^\s*model:\s*){re.escape(model)}\s*$",
            rf"\g<1>{new_model}",
            parts[primary_idx],
            count=1,
            flags=re.M,
        )

        direct_idx = direct_matches[0]
        direct_block = re.sub(
            rf"(^  - model_name:\s*){re.escape(old_direct)}(?=\s|$)",
            rf"\g<1>{new_direct}",
            parts[direct_idx],
            count=1,
            flags=re.M,
        )
        # 无锚点 direct 块包含完整参数副本，需要同步实际 model；有锚点时
        # 参数会自动继承主条目，只更新 direct 分组名即可。
        if "litellm_params: *" not in direct_block:
            direct_block = re.sub(
                rf"(^\s*model:\s*){re.escape(model)}\s*$",
                rf"\g<1>{new_model}",
                direct_block,
                count=1,
                flags=re.M,
            )
        # 同步行尾说明，便于人工检查；它不参与 YAML 语义。
        direct_block = direct_block.replace(f"# {model}", f"# {new_model}", 1)
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
        if item.get("model_name") not in {"fast-pool", "free-pool", "strong-model-pool"}:
            continue
        params = item.get("litellm_params") or {}
        key_ref = params.get("api_key", "")
        env_var = key_ref.split("/", 1)[1] if isinstance(key_ref, str) and key_ref.startswith("os.environ/") else None
        expected = channel_ids.make_direct_model_name(
            params.get("model", ""), params.get("api_base"), env_var
        )
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
    if re.search(r"^\s*priority:\s*-?\d+\s*$", block, re.M):
        new_block = re.sub(r"(^\s*priority:\s*)-?\d+\s*$", rf"\g<1>{new_priority}", block, count=1, flags=re.M)
    else:
        # 这个渠道原来没设 priority（理论上不该发生，config.yaml 里所有渠道
        # 都设了，但防御性地处理一下）：加在这个块的末尾。
        new_block = block.rstrip("\n") + f"\n      priority: {new_priority}\n"

    parts[idx] = new_block

    # 无 YAML 锚点的渠道在 direct-* 中是完整副本，必须同步修改；有锚点的
    # direct 块会通过别名自动继承主条目，无需也无法在块内单独改 priority。
    direct_name = channel_ids.make_direct_model_name(model, api_base, env_var)
    direct_matches = [
        i for i, part in enumerate(parts)
        if re.match(rf"  - model_name:\s*{re.escape(direct_name)}(?:\s|$)", part)
    ]
    if len(direct_matches) != 1:
        return False
    direct_idx = direct_matches[0]
    direct_block = parts[direct_idx]
    if "litellm_params: *" not in direct_block:
        if not re.search(r"^\s*priority:\s*-?\d+\s*$", direct_block, re.M):
            return False
        parts[direct_idx] = re.sub(
            r"(^\s*priority:\s*)-?\d+\s*$",
            rf"\g<1>{new_priority}",
            direct_block,
            count=1,
            flags=re.M,
        )
    safe_rewrite(
        config_path,
        "".join(parts),
        mode=0o640,
        validator=_validate_yaml,
    )
    return True
