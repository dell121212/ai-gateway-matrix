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
  · LiteLLM 的 model_list 和环境变量是启动时加载的，仪表盘保存 key/model
    后要再次执行 bash run.sh 才会生效——这里明确把这一点返回给前端，
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

import asyncio
from contextlib import asynccontextmanager
import hmac
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse
from websockets.asyncio.client import connect as websocket_connect
from websockets.exceptions import ConnectionClosed

from gateway import optimal_channels, usage_tracker
from . import channel_loader, config_editor

_DASHBOARD_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _DASHBOARD_DIR.parent

LITELLM_UPSTREAM_URL = os.environ.get(
    "LITELLM_UPSTREAM_URL", "http://ai-gateway-matrix:4000"
).rstrip("/")
_proxy_client = httpx.AsyncClient(
    timeout=httpx.Timeout(connect=5.0, read=300.0, write=60.0, pool=10.0),
    follow_redirects=False,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await _proxy_client.aclose()


app = FastAPI(
    title="AI Gateway Matrix 中文控制台",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

# 个人模式默认不要求重复输入令牌：Compose 只把 4000/8080 绑定到
# 127.0.0.1。它适用于单用户电脑；多用户主机或局域网部署应将
# DASHBOARD_AUTH 改成 token，恢复独立令牌认证。
DASHBOARD_AUTH = os.environ.get("DASHBOARD_AUTH", "local").strip().lower()
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")
if DASHBOARD_AUTH not in {"local", "token"}:
    raise RuntimeError("DASHBOARD_AUTH 只能是 local 或 token")


def _is_cross_site_browser_request(request: Request) -> bool:
    """拒绝其他网页向本机管理 API 发请求，命令行工具不受影响。"""
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        return True
    origin = request.headers.get("origin")
    if not origin:
        return False
    if DASHBOARD_AUTH == "local":
        return origin.rstrip("/") not in {
            "http://127.0.0.1:4000",
            "http://localhost:4000",
            "http://127.0.0.1:8080",
            "http://localhost:8080",
        }
    expected_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
    return origin.rstrip("/") != expected_origin.rstrip("/")


def _is_dashboard_api_path(path: str) -> bool:
    """仅保护本项目实际占用的管理端点，不拦截 LiteLLM 未来新增的 /api 路径。"""
    return (
        path
        in {
            "/api/auth/verify",
            "/api/channels",
            "/api/client-keys",
            "/api/optimal",
            "/api/summary",
        }
        or path.startswith("/api/channels/")
    )


@app.middleware("http")
async def secure_dashboard(request: Request, call_next):
    is_dashboard_api = _is_dashboard_api_path(request.url.path)
    if is_dashboard_api:
        if _is_cross_site_browser_request(request):
            return JSONResponse(status_code=403, content={"detail": "拒绝跨站访问本机仪表盘"})
        if DASHBOARD_AUTH == "token":
            supplied = request.headers.get("X-Dashboard-Token", "")
            if not DASHBOARD_TOKEN or not hmac.compare_digest(supplied, DASHBOARD_TOKEN):
                return JSONResponse(status_code=401, content={"detail": "仪表盘令牌无效"})

    response = await call_next(request)
    # 只给自有中文控制台加安全头。透明代理的 LiteLLM 路径（例如 /ui）
    # 保留上游自己的 CSP/资源加载规则，避免代理改变原功能。
    is_dashboard_path = (
        request.url.path in {"/", "/healthz"}
        or is_dashboard_api
        or request.url.path.startswith("/static/")
    )
    if is_dashboard_path:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
        )
    if is_dashboard_api:
        response.headers["Cache-Control"] = "no-store"
    return response


class ChannelKeyUpdate(BaseModel):
    value: str = Field(min_length=1, max_length=4096)


class OptimalFlagRequest(BaseModel):
    reason: str = Field(default="", max_length=200)
    expires_in_hours: Optional[float] = Field(default=None, gt=0, le=8760)


class PriorityUpdateRequest(BaseModel):
    priority: int = Field(ge=0, le=1000)


class ModelUpdateRequest(BaseModel):
    model: str = Field(min_length=1, max_length=300)


class ClientKeyCreateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)


@app.get("/healthz")
async def dashboard_health():
    """不暴露管理数据的容器健康检查。"""
    return {"status": "ok"}


@app.get("/api/auth/verify")
async def verify_dashboard_auth():
    return {"authenticated": True, "mode": DASHBOARD_AUTH}


@app.post("/api/client-keys")
async def create_client_key(body: Optional[ClientKeyCreateRequest] = None):
    """创建只允许调用统一路由的客户端 Key，主密钥永不发送到浏览器。"""
    master_key = os.environ.get("GATEWAY_MASTER_KEY", "").strip()
    if not master_key:
        raise HTTPException(status_code=503, detail="网关主密钥尚未初始化，请先运行 bash run.sh")

    requested_name = body.name.strip() if body and body.name else ""
    key_alias = requested_name or datetime.now(timezone.utc).strftime("本机客户端-%Y%m%d-%H%M%S")
    try:
        response = await _proxy_client.post(
            f"{LITELLM_UPSTREAM_URL}/key/generate",
            headers={"Authorization": f"Bearer {master_key}"},
            json={
                "key_alias": key_alias,
                "models": ["auto-route"],
                "rpm_limit": 30,
                "tpm_limit": 100000,
                "duration": "365d",
            },
            timeout=20,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="无法连接网关密钥服务") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"网关创建密钥失败（HTTP {response.status_code}）")

    payload = response.json()
    generated_key = payload.get("key")
    if not generated_key:
        raise HTTPException(status_code=502, detail="网关没有返回新密钥")
    return {
        "key": generated_key,
        "alias": key_alias,
        "models": ["auto-route"],
        "expires_in": "365d",
        "message": "密钥已创建；完整内容只在本页显示这一次，请立即复制保存",
    }


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

    # 已配置渠道优先展示；其内部再按限时优先和人工优先级排序。
    channels.sort(
        key=lambda c: (
            not c["is_configured"],
            not c["is_optimal"],
            c["tier_pool"],
            -(c["priority"] or 0),
        )
    )
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
            "LiteLLM 的渠道列表和环境变量在启动时加载，请在项目目录再次执行 "
            "bash run.sh 应用配置"
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
            "请在项目目录再次执行 bash run.sh 应用配置"
        ),
    }


@app.post("/api/channels/{channel_id:path}/model")
async def update_channel_model(channel_id: str, body: ModelUpdateRequest):
    """让用户填写上游实际提供的模型 ID，不把内置名称当成不可变配置。"""
    channels = {c["channel_id"]: c for c in channel_loader.load_channels()}
    channel = channels.get(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="渠道不存在")

    new_model = body.model.strip()
    try:
        ok = config_editor.update_model(
            channel_loader.CONFIG_PATH,
            pool=channel["tier_pool"],
            model=channel["model"],
            api_base=channel["api_base"],
            env_var=channel["env_var"],
            new_model=new_model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=409, detail="没能在 config.yaml 中唯一定位该渠道，没有修改任何内容")

    return {
        "saved": True,
        "restart_required": True,
        "message": (
            f"模型名称已保存为 {new_model}。请在项目目录再次执行 bash run.sh 应用配置"
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


# HTTP/1.1 逐跳头不能由反向代理继续传递。其余头（尤其 Authorization、
# Content-Type、Accept、OpenAI-*）原样保留，确保现有 SDK 无需改调用方式。
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _connection_header_tokens(headers) -> set[str]:
    return {
        token.strip().lower()
        for token in headers.get("connection", "").split(",")
        if token.strip()
    }


async def _request_body_stream(request: Request):
    async for chunk in request.stream():
        yield chunk


@app.api_route(
    "/{proxy_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy_litellm(request: Request, proxy_path: str):
    """把中文首页之外的全部路径透明转发给 LiteLLM。

    使用原始字节流返回，SSE/流式 completion 不会被 Dashboard 缓冲。
    """
    upstream_url = f"{LITELLM_UPSTREAM_URL}/{proxy_path}"
    if request.url.query:
        upstream_url = f"{upstream_url}?{request.url.query}"

    dynamic_hop_headers = _connection_header_tokens(request.headers)
    request_headers = [
        (name, value)
        for name, value in request.headers.raw
        if name.decode("latin-1").lower()
        not in _HOP_BY_HOP_HEADERS | dynamic_hop_headers | {"host"}
    ]
    has_body = (
        request.headers.get("content-length") not in {None, "0"}
        or "transfer-encoding" in request.headers
    )
    content = _request_body_stream(request) if has_body else (
        b"" if request.method in {"POST", "PUT", "PATCH"} else None
    )

    try:
        upstream_request = _proxy_client.build_request(
            request.method,
            upstream_url,
            headers=request_headers,
            content=content,
        )
        upstream_response = await _proxy_client.send(upstream_request, stream=True)
    except httpx.HTTPError:
        return JSONResponse(
            status_code=502,
            content={"detail": "AI 网关暂时不可达，请稍后重试"},
        )

    response_headers: list[tuple[bytes, bytes]] = []
    dynamic_response_hops = _connection_header_tokens(upstream_response.headers)
    for name, value in upstream_response.headers.multi_items():
        if name.lower() in _HOP_BY_HOP_HEADERS | dynamic_response_hops:
            continue
        if name.lower() == "location" and value.startswith(LITELLM_UPSTREAM_URL):
            value = value[len(LITELLM_UPSTREAM_URL) :] or "/"
        response_headers.append((name.encode("latin-1"), value.encode("latin-1")))

    response = StreamingResponse(
        upstream_response.aiter_raw(),
        status_code=upstream_response.status_code,
        background=BackgroundTask(upstream_response.aclose),
    )
    # Starlette's public ``headers=`` argument is a mapping and therefore cannot
    # represent repeated Set-Cookie/WWW-Authenticate fields.  ASGI raw_headers can.
    response.raw_headers = response_headers
    return response


_WEBSOCKET_HANDSHAKE_HEADERS = {
    "host",
    "connection",
    "upgrade",
    "origin",
    "sec-websocket-key",
    "sec-websocket-version",
    "sec-websocket-extensions",
    "sec-websocket-protocol",
}


async def _safe_websocket_close(websocket: WebSocket, code: int, reason: str = "") -> None:
    try:
        await websocket.close(code=code, reason=reason[:120])
    except RuntimeError:
        pass


@app.websocket("/{proxy_path:path}")
async def proxy_litellm_websocket(websocket: WebSocket, proxy_path: str):
    """Bidirectionally proxy LiteLLM Realtime/Responses WebSocket endpoints."""
    scheme = "wss" if LITELLM_UPSTREAM_URL.startswith("https://") else "ws"
    upstream_base = LITELLM_UPSTREAM_URL.split("://", 1)[-1]
    upstream_url = f"{scheme}://{upstream_base}/{proxy_path}"
    if websocket.url.query:
        upstream_url = f"{upstream_url}?{websocket.url.query}"

    additional_headers = [
        (name.decode("latin-1"), value.decode("latin-1"))
        for name, value in websocket.scope.get("headers", [])
        if name.decode("latin-1").lower() not in _WEBSOCKET_HANDSHAKE_HEADERS
    ]
    subprotocols = list(websocket.scope.get("subprotocols") or [])

    try:
        async with websocket_connect(
            upstream_url,
            origin=websocket.headers.get("origin"),
            subprotocols=subprotocols or None,
            additional_headers=additional_headers,
            open_timeout=10,
            close_timeout=10,
            max_size=None,
            proxy=None,
        ) as upstream:
            await websocket.accept(subprotocol=upstream.subprotocol)

            async def client_to_upstream() -> None:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        await upstream.close(code=message.get("code", 1000))
                        return
                    if message.get("text") is not None:
                        await upstream.send(message["text"])
                    elif message.get("bytes") is not None:
                        await upstream.send(message["bytes"])

            async def upstream_to_client() -> None:
                try:
                    async for message in upstream:
                        if isinstance(message, str):
                            await websocket.send_text(message)
                        else:
                            await websocket.send_bytes(message)
                except ConnectionClosed as exc:
                    await _safe_websocket_close(websocket, exc.code, exc.reason)

            tasks = {
                asyncio.create_task(client_to_upstream()),
                asyncio.create_task(upstream_to_client()),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
    except Exception:
        await _safe_websocket_close(websocket, 1011, "AI 网关 WebSocket 暂时不可达")
