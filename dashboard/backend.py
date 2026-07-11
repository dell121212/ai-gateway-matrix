#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
仪表盘后端 (v2 — 新增限时优先)
————————————————————————————————————————
按你的要求做的浏览器前端配套后端：
  · 解析 config.yaml，把每个 deployment 转成前端可读的"渠道"结构，
    按弱(fast-pool)/中(free-pool)/强(strong-model-pool) 分组
    （实际解析逻辑在 channel_loader.py 里，方便离线单测）
  · 合并 gateway/usage_tracker.py 里的实时用量（这分钟用了几次/多久重置）
  · 合并 gateway/optimal_channels.py 里的"限时优先"标记状态
  · 提供保存 API Key 到 .env 的接口
  · 提供标记/取消"限时优先"的接口（对应"快过期的额度/活动额度，
    优先烧掉"这个需求）

诚实的限制（没有假装成"全自动热更新"）：
  · LiteLLM 的 model_list 是启动时加载的，仪表盘存的 key 要重启
    ai-gateway-matrix 容器才会生效——这里明确把这一点返回给前端，
    而不是假装保存了就立刻生效。
  · "限时优先"标记不需要重启——它存在 Redis 里，gateway/custom_router_hook.py
    每次请求都会实时查询，标记/取消立刻生效。
  · 仪表盘不负责决定某个渠道该属于哪个池子（弱/中/强）——那是
    config.yaml 里 model_name 分组决定的事，改分组需要动 config.yaml
    本身，仪表盘只负责"展示当前是哪个档位 + 帮你填 key + 标记限时优先"。

跟 LiteLLM 自带的 Admin UI（/ui，按花费/请求数看渠道）不是一回事：
LiteLLM 自带的那个是"花了多少钱"视角，对这批 $0.01 dummy 预算的免费渠道
意义不大；这个仪表盘是"还剩多少次调用/什么时候重置"视角，两者互补，
不冲突，可以同时用。
"""

from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from gateway import optimal_channels, usage_tracker
from . import channel_loader, config_editor

_DASHBOARD_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _DASHBOARD_DIR.parent

app = FastAPI(title="AI Gateway Matrix Dashboard")

# 管理面可以写 .env 和 config.yaml，必须使用与网关数据面分离的密钥。
# 不开启 CORS：仪表盘前后端同源，任意 Origin 访问 localhost 反而会
# 给恶意网页留下修改本机 API Key 的攻击面。
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")


@app.middleware("http")
async def secure_dashboard(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        supplied = request.headers.get("X-Dashboard-Token", "")
        if not DASHBOARD_TOKEN or not hmac.compare_digest(supplied, DASHBOARD_TOKEN):
            return JSONResponse(status_code=401, content={"detail": "仪表盘令牌无效"})

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
        "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


class ChannelKeyUpdate(BaseModel):
    value: str = Field(min_length=1, max_length=4096)


class OptimalFlagRequest(BaseModel):
    reason: str = Field(default="", max_length=200)
    expires_in_hours: Optional[float] = Field(default=None, gt=0, le=8760)


class PriorityUpdateRequest(BaseModel):
    priority: int = Field(ge=0, le=1000)


@app.get("/healthz")
async def dashboard_health():
    """不暴露管理数据的容器健康检查。"""
    return {"status": "ok"}


@app.get("/api/auth/verify")
async def verify_dashboard_auth():
    return {"authenticated": True}


@app.get("/api/channels")
async def list_channels():
    channels = channel_loader.load_channels()

    optimal_list = await optimal_channels.list_optimal()
    optimal_by_id = {item["display_id"]: item for item in optimal_list}

    for ch in channels:
        # 注意：这里必须用 usage_key（哈希版本），不是 channel_id（展示用的
        # 稳定主键）——这两个字段長得像但含义不同，之前这里传错过一次
        # （传了 channel_id），会导致用量永远查不到数据，是实测发现的真实 bug。
        ch["usage"] = await usage_tracker.get_usage(ch["usage_key"])

        flag = optimal_by_id.get(ch["channel_id"])
        ch["is_optimal"] = flag is not None
        ch["optimal_reason"] = flag.get("reason") if flag else None
        ch["optimal_seconds_until_expiry"] = flag.get("seconds_until_expiry") if flag else None

    # 排序：限时优先的渠道置顶，然后按档位/优先级排
    channels.sort(key=lambda c: (not c["is_optimal"], c["tier_pool"], -(c["priority"] or 0)))
    return {"channels": channels}


@app.post("/api/channels/{channel_id:path}/key")
async def update_channel_key(channel_id: str, body: ChannelKeyUpdate):
    channels = {c["channel_id"]: c for c in channel_loader.load_channels()}
    channel = channels.get(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="渠道不存在")
    if not channel["env_var"]:
        raise HTTPException(status_code=400, detail="这个渠道没有对应的环境变量，无法通过仪表盘设置")

    value = body.value.strip()
    if not value:
        raise HTTPException(status_code=400, detail="key 不能是空字符串")
    if any(char in value for char in ("\r", "\n", "\x00")):
        raise HTTPException(status_code=400, detail="key 不能包含换行或 NUL")

    channel_loader.write_env_var(channel["env_var"], value)
    return {
        "saved": True,
        "restart_required": True,
        "message": (
            f"已写入 {channel['env_var']}。这一步只更新了 .env 文件，"
            "LiteLLM 的渠道列表是启动时加载的，需要重启网关容器才会生效："
            "docker compose restart ai-gateway-matrix"
        ),
    }


@app.post("/api/channels/{channel_id:path}/priority")
async def update_priority(channel_id: str, body: PriorityUpdateRequest):
    """手动设置某个渠道在它所属档位（弱/中/强）内部的优先级。

    数字越大越优先——这只影响 LiteLLM Router 在同一个档位里挑选具体
    渠道时的倾向，不影响档位本身（弱/中/强）的判断，那是 config.yaml
    里 model_name 分组决定的事，不通过这个接口改。
    """
    channels = {c["channel_id"]: c for c in channel_loader.load_channels()}
    channel = channels.get(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="渠道不存在")

    ok = config_editor.update_priority(
        channel_loader.CONFIG_PATH,
        pool=channel["tier_pool"],
        model=channel["model"],
        api_base=channel["api_base"],
        env_var=channel["env_var"],
        new_priority=body.priority,
    )
    if not ok:
        raise HTTPException(status_code=409, detail="没能在 config.yaml 里唯一定位到这个渠道，没有修改任何内容")

    return {
        "saved": True,
        "restart_required": True,
        "message": (
            f"优先级已改成 {body.priority}。这一步只更新了 config.yaml 文件，"
            "需要重启网关容器才会生效：docker compose restart ai-gateway-matrix"
        ),
    }


@app.post("/api/channels/{channel_id:path}/optimal")
async def flag_optimal(channel_id: str, body: OptimalFlagRequest):
    """标记一个渠道为"限时优先"：非敏感请求会无条件路由到它，直到取消标记、
    过期、或它这分钟的 RPM 被打满（打满时临时跳过，下一分钟窗口恢复后继续用）。"""
    channels = {c["channel_id"]: c for c in channel_loader.load_channels()}
    if channel_id not in channels:
        raise HTTPException(status_code=404, detail="渠道不存在")

    expires_in_seconds = int(body.expires_in_hours * 3600) if body.expires_in_hours else None
    ok = await optimal_channels.set_optimal(channel_id, reason=body.reason.strip(), expires_in_seconds=expires_in_seconds)
    if not ok:
        raise HTTPException(status_code=503, detail="Redis 不可用，无法保存限时优先标记")

    msg = "已标记为限时优先，接下来非敏感请求会优先路由到这个渠道"
    if expires_in_seconds:
        msg += f"，{body.expires_in_hours} 小时后自动失效"
    else:
        msg += "，没有设置过期时间，需要手动取消"
    return {"saved": True, "message": msg}


@app.delete("/api/channels/{channel_id:path}/optimal")
async def unflag_optimal(channel_id: str):
    ok = await optimal_channels.clear_optimal(channel_id)
    if not ok:
        raise HTTPException(status_code=503, detail="Redis 不可用，无法取消限时优先标记")
    return {"saved": True, "message": "已取消限时优先标记，恢复正常的弱/中/强路由"}


@app.get("/api/optimal")
async def list_optimal_flags():
    """当前所有有效的限时优先标记，按最快过期排序（供仪表盘顶部横幅展示）。"""
    return {"flagged": await optimal_channels.list_optimal()}


@app.get("/api/summary")
async def summary():
    channels = channel_loader.load_channels()

    total_tokens_today = 0
    total_cost_today = 0.0
    total_tokens_alltime = 0
    total_cost_alltime = 0.0
    has_any_cost_data = False

    for c in channels:
        usage = await usage_tracker.get_usage(c["usage_key"])
        total_tokens_today += usage.get("day_tokens", 0)
        total_tokens_alltime += usage.get("total_tokens", 0)
        if usage.get("day_cost") is not None:
            total_cost_today += usage["day_cost"]
            has_any_cost_data = True
        if usage.get("total_cost") is not None:
            total_cost_alltime += usage["total_cost"]
            has_any_cost_data = True

    return {
        "total": len(channels),
        "configured": sum(1 for c in channels if c["is_configured"]),
        "by_tier": {
            "强": sum(1 for c in channels if c["tier"] == "强"),
            "中": sum(1 for c in channels if c["tier"] == "中"),
            "弱": sum(1 for c in channels if c["tier"] == "弱"),
        },
        "total_tokens_today": total_tokens_today,
        "total_tokens_alltime": total_tokens_alltime,
        # 金额是 None 还是 0：如果一次花费数据都没采集到（既没有 litellm 精确计价，
        # 也没有估算表命中），就是 None，不能显示成 "$0.00"——那看起来像是
        # "查过了确实不要钱"，跟"压根没数据"是两回事。
        "total_cost_today": total_cost_today if has_any_cost_data else None,
        "total_cost_alltime": total_cost_alltime if has_any_cost_data else None,
    }


_STATIC_DIR = _DASHBOARD_DIR / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/")
async def index():
    index_file = _STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="前端静态文件未找到")
    return FileResponse(str(index_file))
