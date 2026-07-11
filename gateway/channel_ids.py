#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
渠道标识生成函数 (v1)
————————————————————————————————————————
"限时优先"功能（v6）需要三个地方对同一个渠道算出一致的标识：
  1. config.yaml 里 "direct-xxxxxxxxxx" 这个 model_name 字符串本身
     （写死在 YAML 里，因为 YAML 没法在解析时调用 Python 函数）
  2. gateway/custom_router_hook.py 启动时解析 config.yaml，需要独立算出
     同一个 direct_model_name，才能在"限时优先"命中时把 data["model"]
     改写成正确的值
  3. dashboard/channel_loader.py 展示渠道列表、以及用户点"标记为限时优先"
     时，需要算出同一个 display_id 存进 Redis

三边都从同一份 (model, api_base, env_var) 出发、用同一个函数算 id，
就不会出现"仪表盘标记的是 A，hook 路由到的是 B"这种错位。
"""

from __future__ import annotations

import hashlib
from typing import Optional


def make_display_id(model: str, api_base: Optional[str], env_var: Optional[str]) -> str:
    """稳定的展示/API 用主键，不依赖是否已经配置了真实 key。

    跟 dashboard/channel_loader.py 里原来单独写的那份逻辑保持一致
    （这里抽成共享函数，避免两边各写一份、以后改一个忘了改另一个）。
    """
    return f"{model}@{api_base or 'default'}#{env_var or 'no-env'}"


def make_direct_model_name(model: str, api_base: Optional[str], env_var: Optional[str]) -> str:
    """"限时优先"用的直连 model_name（对应 config.yaml 里的一个只有单个
    deployment 的 model_name 分组），保证 Router 收到这个 model 名字时
    100% 精确路由到这一个渠道，而不是又走一次池子内部的负载均衡。

    用哈希而不是拼接原始字符串，是因为 model/api_base 里可能出现 YAML
    和 URL 都不一定 100% 安全的字符（冒号、斜杠、点），哈希后是纯字母
    数字，不管是写进 YAML 的 model_name 还是用在 REST API 路径里都不用
    转义。哈希不可逆，但这里也不需要可逆——只要三边算出来一致就够用。
    """
    raw = f"{model}|{api_base or ''}|{env_var or ''}"
    return "direct-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
