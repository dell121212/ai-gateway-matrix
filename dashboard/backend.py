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

热加载（无需再跑 bash run.sh）：
  · 保存 Key / 改模型 / 改优先级后，写 reload 信号；网关后台轮询 + 下次请求
    会同步 .env 与 config.yaml 并重建 Router（gateway/env_sync.py）。
  · "限时优先"存在 Redis，即时生效。
  · 渠道属于哪一档仍由 config.yaml 的 model_name 决定；仪表盘负责填 Key、
    改模型名与优先级展示。

跟 LiteLLM 自带的 Admin UI（/ui，按花费/请求数看渠道）不是一回事：
LiteLLM 自带的那个是"花了多少钱"视角，对这批 $0.01 dummy 预算的免费渠道
意义不大；这个仪表盘是"还剩多少次调用/什么时候重置"视角，两者互补，
不冲突，可以同时用。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hmac
import logging
import os
import uuid
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

from gateway import env_sync, optimal_channels, pricing, priority_overrides, usage_tracker
from . import (
    balance_probe,
    channel_loader,
    client_keys_store,
    config_editor,
    connection_status_store,
    quota_catalog,
    settings_store,
)

logger = logging.getLogger("ai_gateway_matrix.dashboard")

_DASHBOARD_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _DASHBOARD_DIR.parent

LITELLM_UPSTREAM_URL = os.environ.get(
    "LITELLM_UPSTREAM_URL", "http://ai-gateway-matrix:4000"
).rstrip("/")
_proxy_client = httpx.AsyncClient(
    timeout=httpx.Timeout(connect=5.0, read=300.0, write=60.0, pool=10.0),
    follow_redirects=False,
    # 这里只访问 Docker 内部网关；继承宿主 HTTP_PROXY/ALL_PROXY 会让本地
    # socks:// 代理在模块导入阶段触发 Unknown scheme，并可能把内部流量外送。
    trust_env=False,
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
            "/api/settings",
            "/api/gateway/probe",
        }
        or path.startswith("/api/channels/")
        or path.startswith("/api/client-keys/")
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


class SettingsUpdateRequest(BaseModel):
    theme: Optional[str] = None
    language: Optional[str] = None
    autostart: Optional[bool] = None
    autostart_silent: Optional[bool] = None


@app.get("/healthz")
async def dashboard_health():
    """不暴露管理数据的容器健康检查。"""
    return {"status": "ok"}


@app.get("/api/auth/verify")
async def verify_dashboard_auth():
    return {"authenticated": True, "mode": DASHBOARD_AUTH}


def _master_key() -> str:
    key = os.environ.get("GATEWAY_MASTER_KEY", "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="网关主密钥尚未初始化，请先运行 bash run.sh")
    return key


@app.get("/api/client-keys")
async def list_client_keys():
    """列出客户端 Key：以项目登记簿为准（可再次复制），网关库补充元数据。

    不依赖浏览器 localStorage/sessionStorage；完整密钥只在
    state/client-keys.json（0600）与创建时响应中出现。
    """
    master_key = os.environ.get("GATEWAY_MASTER_KEY", "").strip()
    remote: list[dict] = []
    if master_key:
        try:
            response = await _proxy_client.get(
                f"{LITELLM_UPSTREAM_URL}/key/list",
                headers={"Authorization": f"Bearer {master_key}"},
                params={"return_full_object": "true", "page": 1, "size": 100},
                timeout=15,
            )
            if response.status_code < 400:
                payload = response.json()
                items = payload.get("keys") or []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    remote.append({
                        "id": item.get("token") or item.get("key_name"),
                        "alias": item.get("key_alias") or item.get("key_name") or "未命名",
                        "key_preview": item.get("key_name") or "sk-…",
                        "models": item.get("models") or ["auto-route"],
                        "rpm_limit": item.get("rpm_limit"),
                        "tpm_limit": item.get("tpm_limit"),
                        "expires_in": item.get("expires"),
                        "created_at": item.get("created_at"),
                        "spend": item.get("spend"),
                        "source": "litellm",
                        "has_secret": False,
                    })
        except httpx.HTTPError:
            pass

    # 项目记忆优先：本地登记簿有完整密钥，必须排在前面并可复制
    local = client_keys_store.list_local_keys(include_secret=False)
    remote_by_id = {r.get("id"): r for r in remote if r.get("id")}
    remote_by_alias = {r.get("alias"): r for r in remote if r.get("alias")}

    merged: list[dict] = []
    seen_ids: set[str] = set()
    seen_aliases: set[str] = set()

    for loc in local:
        lid = loc.get("id") or ""
        alias = loc.get("alias") or ""
        meta = remote_by_id.get(lid) or remote_by_alias.get(alias) or {}
        row = {
            **loc,
            "has_secret": True,
            "local_id": lid,
            "persisted": True,
            "store": "project",
            "spend": meta.get("spend"),
            "source": "project+gateway" if meta else "project",
        }
        if meta.get("expires_in") and not row.get("expires_in"):
            row["expires_in"] = meta.get("expires_in")
        if meta.get("key_preview"):
            # 展示网关的 sk-… 预览亦可，但以本地 mask 为准即可
            pass
        if meta.get("rpm_limit") is not None:
            row["rpm_limit"] = meta.get("rpm_limit")
        merged.append(row)
        if lid:
            seen_ids.add(lid)
        if alias:
            seen_aliases.add(alias)

    for rem in remote:
        rid = rem.get("id") or ""
        alias = rem.get("alias") or ""
        if rid in seen_ids or alias in seen_aliases:
            continue
        rem = {
            **rem,
            "has_secret": False,
            "persisted": True,
            "store": "gateway_only",
            "local_id": None,
        }
        merged.append(rem)

    store = client_keys_store.store_path()
    return {
        "keys": merged,
        "api_base": "http://127.0.0.1:4000/v1",
        "recommended_model": "auto-route",
        "store_path": store,
        "message": (
            "完整密钥保存在项目文件 state/client-keys.json（非浏览器缓存）；"
            "上游渠道 Key 保存在项目 .env。换浏览器/清缓存后仍可从本页列表复制。"
        ),
    }


def _litellm_error_detail(response: httpx.Response) -> str:
    """从 LiteLLM 错误响应里抽出可读原因。"""
    try:
        payload = response.json()
    except Exception:
        text = (response.text or "").strip()
        return text[:300] if text else f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        for key in ("error", "detail", "message"):
            val = payload.get(key)
            if isinstance(val, dict):
                msg = val.get("message") or val.get("detail") or val.get("error")
                if msg:
                    return str(msg)[:300]
            if val:
                return str(val)[:300]
    return f"HTTP {response.status_code}"


def _unique_key_alias(display_name: str) -> str:
    """LiteLLM 要求全局唯一 key_alias；同秒连点会撞车，故始终加时间+随机后缀。"""
    base = (display_name or "本机客户端").strip() or "本机客户端"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{base}-{stamp}-{uuid.uuid4().hex[:6]}"


@app.post("/api/client-keys")
async def create_client_key(body: Optional[ClientKeyCreateRequest] = None):
    """创建只允许调用统一路由的客户端 Key；并写入本机登记簿便于再次复制。"""
    master_key = _master_key()

    requested_name = body.name.strip() if body and body.name else ""
    if requested_name and not client_keys_store.is_safe_alias(requested_name):
        raise HTTPException(status_code=400, detail="密钥名称包含非法字符")

    display_name = requested_name or "本机客户端"
    # 综合入口聚合多家上游：客户端 Key 的 RPM/TPM 是「整网关」上限，
    # 绝不能按单厂 free 层 30 RPM 写死。可用环境变量覆盖。
    try:
        rpm_limit = max(1, int(os.environ.get("CLIENT_KEY_DEFAULT_RPM", "1200") or "1200"))
    except (TypeError, ValueError):
        rpm_limit = 1200
    try:
        tpm_limit = max(1000, int(os.environ.get("CLIENT_KEY_DEFAULT_TPM", "2000000") or "2000000"))
    except (TypeError, ValueError):
        tpm_limit = 2_000_000
    duration = (os.environ.get("CLIENT_KEY_DEFAULT_DURATION", "365d") or "365d").strip()
    # 允许智能/弱/中/强/顶级入口（与 config 中 model_name 对齐）
    allowed_models = [
        "auto-route",
        "mode-intelligent",
        "mode-weak",
        "mode-mid",
        "mode-strong",
        "mode-elite",
        "fast-pool",
        "free-pool",
        "strong-model-pool",
        "elite-model-pool",
    ]

    response = None
    key_alias = ""
    last_detail = ""
    # 别名冲突时自动换后缀重试（LiteLLM: Unique key aliases required）
    for attempt in range(4):
        key_alias = _unique_key_alias(display_name)
        try:
            response = await _proxy_client.post(
                f"{LITELLM_UPSTREAM_URL}/key/generate",
                headers={"Authorization": f"Bearer {master_key}"},
                json={
                    "key_alias": key_alias,
                    "models": allowed_models,
                    "rpm_limit": rpm_limit,
                    "tpm_limit": tpm_limit,
                    "duration": duration,
                },
                timeout=20,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="无法连接网关密钥服务") from exc
        if response.status_code < 400:
            break
        last_detail = _litellm_error_detail(response)
        conflict = response.status_code == 400 and (
            "already exists" in last_detail.lower()
            or "unique key alias" in last_detail.lower()
            or "别名" in last_detail
        )
        if conflict and attempt < 3:
            logger.warning("key_alias 冲突，重试: %s (%s)", key_alias, last_detail)
            continue
        raise HTTPException(
            status_code=502,
            detail=f"网关创建密钥失败：{last_detail}",
        )

    assert response is not None
    try:
        payload = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="网关返回了无法解析的响应") from exc

    generated_key = payload.get("key")
    if not generated_key:
        raise HTTPException(status_code=502, detail="网关没有返回新密钥")

    token_hash = payload.get("token") if isinstance(payload.get("token"), str) else None
    # 登记簿写失败不应吞掉已创建的密钥（以前 state 只读会导致 500，前端以为失败）
    store_warning = ""
    try:
        stored = client_keys_store.remember_key(
            full_key=generated_key,
            alias=key_alias,
            models=allowed_models,
            rpm_limit=rpm_limit,
            tpm_limit=tpm_limit,
            expires_in=duration,
            token_hash=token_hash,
        )
    except OSError as exc:
        logger.exception("写入 client-keys 登记簿失败")
        store_warning = f"（本机登记簿未写入：{exc}；请立即复制密钥）"
        stored = {
            "id": token_hash or generated_key[-16:],
            "key_preview": client_keys_store.mask_key(generated_key),
        }

    return {
        "key": generated_key,
        "alias": key_alias,
        "id": stored.get("id"),
        "key_preview": stored.get("key_preview"),
        "models": allowed_models,
        "rpm_limit": rpm_limit,
        "tpm_limit": tpm_limit,
        "expires_in": duration,
        "message": (
            "密钥已写入项目 state/client-keys.json（非浏览器）。"
            f"综合入口限额约 {rpm_limit} RPM / {tpm_limit} TPM（聚合多家上游，非单厂 30 RPM）。"
            "下方列表下次打开仍在，可随时点「复制」。"
            + store_warning
        ),
        "persisted": not bool(store_warning),
        "store_path": client_keys_store.store_path(),
    }


@app.post("/api/client-keys/raise-limits")
async def raise_all_client_key_limits():
    """把已有客户端 Key 的 RPM/TPM 提到多厂聚合默认值（修历史 30 RPM 写死）。"""
    master_key = _master_key()
    try:
        rpm_limit = max(1, int(os.environ.get("CLIENT_KEY_DEFAULT_RPM", "1200") or "1200"))
    except (TypeError, ValueError):
        rpm_limit = 1200
    try:
        tpm_limit = max(1000, int(os.environ.get("CLIENT_KEY_DEFAULT_TPM", "2000000") or "2000000"))
    except (TypeError, ValueError):
        tpm_limit = 2_000_000

    rows = client_keys_store.list_local_keys(include_secret=True)
    updated = 0
    errors: list[str] = []
    default_models = [
        "auto-route",
        "mode-intelligent",
        "mode-weak",
        "mode-mid",
        "mode-strong",
        "mode-elite",
        "fast-pool",
        "free-pool",
        "strong-model-pool",
        "elite-model-pool",
    ]
    for row in rows:
        full = row.get("key") or ""
        if not full:
            continue
        try:
            response = await _proxy_client.post(
                f"{LITELLM_UPSTREAM_URL}/key/update",
                headers={"Authorization": f"Bearer {master_key}"},
                json={
                    "key": full,
                    "rpm_limit": rpm_limit,
                    "tpm_limit": tpm_limit,
                    "models": row.get("models") or default_models,
                },
                timeout=20,
            )
            if response.status_code >= 400:
                errors.append(f"{row.get('alias')}: {_litellm_error_detail(response)}")
                continue
            client_keys_store.update_key_meta(
                row.get("id") or "",
                rpm_limit=rpm_limit,
                tpm_limit=tpm_limit,
                models=row.get("models") or default_models,
            )
            updated += 1
        except Exception as exc:
            errors.append(f"{row.get('alias')}: {type(exc).__name__}: {exc}")

    return {
        "updated": updated,
        "rpm_limit": rpm_limit,
        "tpm_limit": tpm_limit,
        "errors": errors[:10],
        "message": f"已将 {updated} 个客户端密钥限额提升为 {rpm_limit} RPM / {tpm_limit} TPM",
    }


@app.get("/api/client-keys/{key_id}/reveal")
async def reveal_client_key(key_id: str):
    secret = client_keys_store.reveal_key(key_id)
    if not secret:
        raise HTTPException(status_code=404, detail="本机登记簿中没有该密钥的完整内容")
    return {"key": secret, "id": key_id}


def _gateway_v1_base() -> str:
    """Dashboard 容器内探测须走 Docker 内网 LiteLLM，不能写宿主机 127.0.0.1:4000。"""
    return f"{LITELLM_UPSTREAM_URL.rstrip('/')}/v1"


@app.post("/api/client-keys/{key_id}/probe")
async def probe_client_key(key_id: str):
    """用本机登记簿中的客户端 Key 打 auto-route，验证「项目生产的 API」是否可用。"""
    secret = client_keys_store.reveal_key(key_id)
    if not secret:
        raise HTTPException(status_code=404, detail="无法读取该密钥，请重新创建或确认登记簿")
    return await _probe_chat(
        base_url=_gateway_v1_base(),
        api_key=secret,
        model="auto-route",
        label="本机统一 API",
    )


@app.delete("/api/client-keys/{key_id}")
async def delete_client_key(key_id: str):
    """删除客户端 Key：项目登记簿 + 网关库（可创建即可删除）。"""
    master_key = os.environ.get("GATEWAY_MASTER_KEY", "").strip()
    local = client_keys_store.list_local_keys(include_secret=False)
    match = next(
        (k for k in local if k.get("id") == key_id or k.get("alias") == key_id),
        None,
    )
    alias = (match or {}).get("alias") or key_id
    token = (match or {}).get("id") or key_id

    gateway_deleted = False
    gateway_detail = ""
    if master_key:
        try:
            # LiteLLM: body 需 keys 或 key_aliases
            payload = {"keys": [token], "key_aliases": [alias]}
            response = await _proxy_client.post(
                f"{LITELLM_UPSTREAM_URL}/key/delete",
                headers={"Authorization": f"Bearer {master_key}"},
                json=payload,
                timeout=20,
            )
            if response.status_code < 400:
                gateway_deleted = True
            else:
                # 再试仅 alias
                response2 = await _proxy_client.post(
                    f"{LITELLM_UPSTREAM_URL}/key/delete",
                    headers={"Authorization": f"Bearer {master_key}"},
                    json={"key_aliases": [alias]},
                    timeout=20,
                )
                if response2.status_code < 400:
                    gateway_deleted = True
                else:
                    gateway_detail = _litellm_error_detail(response2)
        except httpx.HTTPError as exc:
            gateway_detail = str(exc)

    local_deleted = client_keys_store.revoke_local(token)
    if not local_deleted and match:
        local_deleted = client_keys_store.revoke_local(match.get("id") or "")
    # 也按 alias 清本地
    if not local_deleted:
        data_keys = client_keys_store.list_local_keys(include_secret=False)
        for k in data_keys:
            if k.get("alias") == alias:
                local_deleted = client_keys_store.revoke_local(k.get("id") or "")
                break

    if not gateway_deleted and not local_deleted:
        raise HTTPException(
            status_code=404,
            detail=gateway_detail or "未找到可删除的密钥（项目登记簿与网关均无匹配项）",
        )
    parts = []
    if local_deleted:
        parts.append("已从项目登记簿移除")
    if gateway_deleted:
        parts.append("已从网关作废")
    elif gateway_detail:
        parts.append(f"网关侧：{gateway_detail}")
    return {
        "deleted": True,
        "local_deleted": local_deleted,
        "gateway_deleted": gateway_deleted,
        "message": "；".join(parts) or "已删除",
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
        ch["rate_limits"] = quota_catalog.build_rate_limits(
            ch.get("env_var"),
            ch.get("rpm_limit"),
            usage=ch["usage"],
        )
        try:
            ch["smoothness"] = await usage_tracker.get_smoothness(ch["usage_key"])
        except Exception:
            ch["smoothness"] = {
                "available": False,
                "label": "学习中",
                "label_level": "unknown",
                "hint_zh": "",
            }

        flag = optimal_by_id.get(ch["channel_id"])
        ch["is_optimal"] = flag is not None
        ch["optimal_reason"] = flag.get("reason") if flag else None
        ch["optimal_seconds_until_expiry"] = flag.get("seconds_until_expiry") if flag else None

        # 按量/余额：官方接口 + 本机消耗（已配置才查官方，带短缓存）
        try:
            ch["billing_info"] = await balance_probe.fetch_balance_for_channel(ch)
        except Exception as exc:
            logger.debug("billing_info 跳过 %s: %s", ch.get("env_var"), exc)
            ch["billing_info"] = {
                "billing": ch.get("billing") or "free_or_trial",
                "supports_official_balance": False,
                "message": "余额查询暂不可用",
            }

        # 最近一次连通性（检查连接 / 保存 Key 探测）
        company_id = ch.get("company_id") or ch.get("env_var") or ""
        conn = connection_status_store.get_company(company_id) or {}
        ch["connection_ok"] = conn.get("ok") if conn else None
        ch["connection_checked_at"] = conn.get("checked_at")
        ch["connection_message"] = conn.get("message") or ""
        # 若从未探测，用当日成功调用推断「曾连通」
        if ch["connection_ok"] is None:
            u = ch.get("usage") or {}
            if u.get("available") and int(u.get("successful_calls_today") or 0) > 0:
                ch["connection_ok"] = True
                ch["connection_message"] = "今日有成功调用"

    # 人工优先级是绝对第一排序键；同分时再比较配置、限时优先和档位。
    channels.sort(
        key=lambda c: (
            -(c["priority"] or 0),
            not c["is_configured"],
            not c["is_optimal"],
            c["tier_pool"],
        )
    )
    return {"channels": channels}


@app.post("/api/channels/{channel_id:path}/balance")
async def refresh_channel_balance(channel_id: str):
    """强制刷新某渠道官方余额（清缓存后重查）。"""
    channel = channel_loader.find_channel(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="渠道不存在")
    if not channel.get("is_configured"):
        raise HTTPException(status_code=400, detail="尚未配置 API Key")
    channel["usage"] = await usage_tracker.get_usage(channel["usage_key"])
    # 清缓存
    env_var = channel.get("env_var") or ""
    mask = channel.get("masked_key") or ""
    balance_probe._CACHE.pop(f"{env_var}:{mask}", None)
    info = await balance_probe.fetch_balance_for_channel(channel)
    return {"channel_id": channel_id, "billing_info": info}


@app.get("/api/settings")
async def get_settings():
    return settings_store.get_settings()


@app.put("/api/settings")
async def put_settings(body: SettingsUpdateRequest):
    try:
        return settings_store.update_settings(body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/gateway/probe")
async def probe_gateway():
    """检查本机统一入口（健康检查 + 可选 models 列表）。"""
    results = []
    try:
        live = await _proxy_client.get(f"{LITELLM_UPSTREAM_URL}/health/liveliness", timeout=8)
        results.append({
            "step": "liveliness",
            "ok": live.status_code < 400,
            "detail": f"HTTP {live.status_code}",
        })
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "message": "网关不可达",
            "results": [{"step": "liveliness", "ok": False, "detail": str(exc)}],
        }

    master = os.environ.get("GATEWAY_MASTER_KEY", "").strip()
    if master:
        try:
            models = await _proxy_client.get(
                f"{LITELLM_UPSTREAM_URL}/v1/models",
                headers={"Authorization": f"Bearer {master}"},
                timeout=12,
            )
            results.append({
                "step": "models",
                "ok": models.status_code < 400,
                "detail": f"HTTP {models.status_code}",
            })
        except httpx.HTTPError as exc:
            results.append({"step": "models", "ok": False, "detail": str(exc)})

    ok = all(r.get("ok") for r in results)
    return {
        "ok": ok,
        "message": "统一入口正常" if ok else "统一入口异常",
        "api_base": "http://127.0.0.1:4000/v1",
        "results": results,
    }


def _sanitize_api_key_for_header(api_key: str) -> tuple[str, Optional[str]]:
    """HTTP Authorization 头只能是 latin-1/ASCII。

    用户从网页复制时常带入中文引号、零宽字符等，httpx 会抛 UnicodeEncodeError → 整请求 500。
    返回 (清理后的 key, 可选警告)。
    """
    raw = (api_key or "").strip()
    # 去掉常见粘贴杂质
    for ch in (
        "\u200b", "\u200c", "\u200d", "\ufeff",  # 零宽
        "\u00a0",  # nbsp
        "“", "”", "‘", "’", "「", "」", "『", "』",
        '"', "'", "`",
    ):
        raw = raw.replace(ch, "")
    raw = raw.strip()
    try:
        raw.encode("ascii")
        return raw, None
    except UnicodeEncodeError:
        cleaned = raw.encode("ascii", "ignore").decode("ascii").strip()
        if not cleaned:
            return "", "API Key 含非法字符且清理后为空，请重新粘贴纯英文/数字密钥"
        return cleaned, "已自动去掉 Key 中的非 ASCII 字符"


async def _probe_chat(*, base_url: str, api_key: str, model: str, label: str) -> dict:
    url = base_url.rstrip("/") + "/chat/completions"
    key, key_warn = _sanitize_api_key_for_header(api_key)
    if not key:
        return {
            "ok": False,
            "label": label,
            "model": model,
            "message": key_warn or "API Key 无效",
            "latency_ms": None,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
    started = datetime.now(timezone.utc)
    try:
        response = await _proxy_client.post(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "temperature": 0,
            },
            timeout=45,
        )
    except UnicodeEncodeError as exc:
        return {
            "ok": False,
            "label": label,
            "model": model,
            "message": f"Key 含无法放入 HTTP 头的字符：{exc}",
            "latency_ms": None,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "label": label,
            "model": model,
            "message": f"连接失败：{exc}",
            "latency_ms": None,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
    except Exception as exc:
        # 探测绝不向上抛 → 避免保存接口变成 HTTP 500
        return {
            "ok": False,
            "label": label,
            "model": model,
            "message": f"探测异常：{type(exc).__name__}: {exc}",
            "latency_ms": None,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
    latency = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    ok = response.status_code < 400
    detail = ""
    prompt_tokens = 0
    completion_tokens = 0
    try:
        body = response.json()
        if not ok:
            err = body.get("error") if isinstance(body, dict) else None
            if isinstance(err, dict):
                detail = str(err.get("message") or err)[:240]
            else:
                detail = str(body.get("error") or body.get("detail") or body.get("message") or body)[:240]
        else:
            detail = "chat/completions 成功"
            usage = body.get("usage") if isinstance(body, dict) else None
            if isinstance(usage, dict):
                prompt_tokens = int(usage.get("prompt_tokens") or 0)
                completion_tokens = int(usage.get("completion_tokens") or 0)
    except Exception:
        detail = (response.text or "")[:240]
    if key_warn:
        detail = f"{detail}（{key_warn}）" if detail else key_warn
    # 探测成功但上游不回 usage：仍记最小消耗，保证「检查连接」进入统计
    if ok and prompt_tokens + completion_tokens <= 0:
        prompt_tokens, completion_tokens = 8, 1
    return {
        "ok": ok,
        "label": label,
        "model": model,
        "http_status": response.status_code,
        "latency_ms": latency,
        "message": detail if detail else ("正常" if ok else "失败"),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }


async def _record_probe_usage(channel: dict, result: dict) -> None:
    """把检查连接/探测写入 usage_tracker（与正式请求同一套账本）。"""
    try:
        usage_key = channel.get("usage_key")
        if not usage_key:
            # 与 channel_loader 一致：用真实 key 哈希
            env_values = channel_loader.read_env_file()
            raw_key = (env_values.get(channel.get("env_var") or "") or "").strip()
            usage_key = usage_tracker.make_channel_id(
                channel.get("model") or result.get("model") or "",
                channel.get("api_base"),
                raw_key or None,
            )
        ok = bool(result.get("ok"))
        pt = int(result.get("prompt_tokens") or 0)
        ct = int(result.get("completion_tokens") or 0)
        cost = None
        cost_source = "unknown"
        if ok and (pt + ct) > 0:
            model = channel.get("model") or result.get("model") or ""
            cost, cost_source = pricing.compute_cost(
                model,
                object(),  # 无完整 response 时走估算表/官方表
                pt,
                ct,
                api_base=channel.get("api_base"),
            )
        await usage_tracker.record_call(
            usage_key,
            success=ok,
            prompt_tokens=pt if ok else 0,
            completion_tokens=ct if ok else 0,
            cost=cost,
            cost_source=cost_source,
            latency_ms=float(result["latency_ms"]) if result.get("latency_ms") is not None else None,
            error_class=None if ok else "probe_fail",
        )
    except Exception as exc:
        logger.debug("探测用量记账跳过: %s", exc)


def _resolve_openai_base(channel: dict) -> Optional[str]:
    """尽量把 litellm model 映射到可直接探测的 OpenAI 兼容 base。"""
    api_base = (channel.get("api_base") or "").rstrip("/")
    if api_base:
        return api_base if api_base.endswith("/v1") or "/paas/" in api_base or api_base.endswith("/openai") else api_base
    model = channel.get("model") or ""
    env_var = (channel.get("env_var") or "").upper()
    # litellm 内置 provider 前缀 / 常见厂商
    defaults = {
        "groq/": "https://api.groq.com/openai/v1",
        "cerebras/": "https://api.cerebras.ai/v1",
        "sambanova/": "https://api.sambanova.ai/v1",
        "mistral/": "https://api.mistral.ai/v1",
        "openrouter/": "https://openrouter.ai/api/v1",
        "deepseek/": "https://api.deepseek.com/v1",
        "gemini/": "https://generativelanguage.googleapis.com/v1beta/openai",
        "together_ai/": "https://api.together.xyz/v1",
        "together/": "https://api.together.xyz/v1",
        "fireworks_ai/": "https://api.fireworks.ai/inference/v1",
        "fireworks/": "https://api.fireworks.ai/inference/v1",
        "moonshot/": "https://api.moonshot.cn/v1",
        "dashscope/": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "siliconflow/": "https://api.siliconflow.cn/v1",
        "huggingface/": "https://api-inference.huggingface.co/v1",
        "deepinfra/": "https://api.deepinfra.com/v1/openai",
        "novita/": "https://api.novita.ai/v3/openai",
        "nvidia_nim/": "https://integrate.api.nvidia.com/v1",
        "openai/": "https://api.openai.com/v1",
    }
    for prefix, base in defaults.items():
        if model.startswith(prefix):
            return base
    # 按 env 名兜底
    by_env = {
        "GROQ_API_KEY": "https://api.groq.com/openai/v1",
        "CEREBRAS_API_KEY": "https://api.cerebras.ai/v1",
        "SAMBANOVA_API_KEY": "https://api.sambanova.ai/v1",
        "MISTRAL_KEY_1": "https://api.mistral.ai/v1",
        "MISTRAL_KEY_2": "https://api.mistral.ai/v1",
        "OPENROUTER_API_KEY": "https://openrouter.ai/api/v1",
        "DEEPSEEK_API_KEY": "https://api.deepseek.com/v1",
        "GEMINI_API_KEY": "https://generativelanguage.googleapis.com/v1beta/openai",
        "TOGETHER_API_KEY": "https://api.together.xyz/v1",
        "FIREWORKS_API_KEY": "https://api.fireworks.ai/inference/v1",
        "MOONSHOT_API_KEY": "https://api.moonshot.cn/v1",
        "DASHSCOPE_API_KEY": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "SILICONFLOW_API_KEY": "https://api.siliconflow.cn/v1",
        "GLM_API_KEY": "https://open.bigmodel.cn/api/paas/v4",
        "HF_TOKEN": "https://api-inference.huggingface.co/v1",
        "DEEPINFRA_API_KEY": "https://api.deepinfra.com/v1/openai",
        "NOVITA_API_KEY": "https://api.novita.ai/v3/openai",
        "NVIDIA_API_KEY": "https://integrate.api.nvidia.com/v1",
        "GITHUB_TOKEN": "https://models.github.ai/inference",
    }
    return by_env.get(env_var)


def _normalize_probe_base(base: str) -> str:
    base = (base or "").rstrip("/")
    if not base:
        return base
    if base.endswith("/v1") or base.endswith("/openai") or "/paas/" in base or "/compatible-mode/" in base:
        return base
    return base + "/v1"


def _resolve_probe_model(channel: dict) -> str:
    model = channel.get("model") or ""
    # OpenAI 兼容直连时通常去掉 provider 前缀
    known = {
        "groq", "cerebras", "sambanova", "mistral", "openrouter", "gemini",
        "deepseek", "together_ai", "together", "fireworks_ai", "fireworks",
        "moonshot", "dashscope", "siliconflow", "huggingface", "deepinfra",
        "novita", "nvidia_nim", "openai",
    }
    if "/" in model:
        parts = model.split("/", 1)
        if parts[0] in known:
            return parts[1]
    return model


async def _probe_channel_connection(
    channel: dict,
    *,
    api_key: Optional[str] = None,
) -> dict:
    """对单个上游渠道做真实连通性探测（可用刚保存的 key，不必等重启）。"""
    env_var = channel.get("env_var")
    if not env_var:
        return {
            "ok": False,
            "label": channel.get("provider_name") or "渠道",
            "message": "该渠道无环境变量，无法探测",
        }
    if api_key is None:
        env_values = channel_loader.read_env_file()
        api_key = (env_values.get(env_var) or "").strip()
    api_key = (api_key or "").strip()
    if not api_key or api_key.startswith("dummy-"):
        return {
            "ok": False,
            "label": channel.get("provider_name") or env_var,
            "message": "尚未配置有效 API Key",
        }

    base = _resolve_openai_base(channel)
    model = _resolve_probe_model(channel)
    label = channel.get("provider_name") or env_var
    if not base:
        # 回退：经网关 direct 模型（需已 run.sh 加载 key；探测用刚写的 key 时走直连更准）
        master = os.environ.get("GATEWAY_MASTER_KEY", "").strip()
        if not master:
            return {
                "ok": False,
                "label": label,
                "model": model,
                "message": "无法解析上游地址，请检查 config 中的 api_base / model 前缀",
            }
        direct = channel.get("direct_model_name") or model
        result = await _probe_chat(
            base_url=_gateway_v1_base(),
            api_key=master,
            model=direct,
            label=label,
        )
        await _record_probe_usage(channel, result)
        return result

    result = await _probe_chat(
        base_url=_normalize_probe_base(base),
        api_key=api_key,
        model=model,
        label=label,
    )
    await _record_probe_usage(channel, result)
    return result


@app.post("/api/channels/{channel_id:path}/probe")
async def probe_channel(channel_id: str):
    """检查已写入 .env 的上游 Key 是否真正能连通。"""
    channel = channel_loader.find_channel(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="渠道不存在")
    result = await _probe_channel_connection(channel)
    # 未配置时用 400 便于前端区分
    if result.get("message") == "尚未配置有效 API Key":
        raise HTTPException(status_code=400, detail=result["message"])
    try:
        connection_status_store.record(
            company_id=channel.get("company_id") or channel.get("env_var") or "",
            channel_id=channel.get("channel_id") or channel_id,
            env_var=channel.get("env_var") or "",
            ok=bool(result.get("ok")),
            message=str(result.get("message") or ""),
            latency_ms=result.get("latency_ms"),
        )
    except Exception:
        pass
    result["connection_ok"] = bool(result.get("ok"))
    return result


@app.post("/api/channels/{channel_id:path}/key")
async def update_channel_key(channel_id: str, body: ChannelKeyUpdate):
    channel = channel_loader.find_channel(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="渠道不存在")
    if not channel["env_var"]:
        raise HTTPException(status_code=400, detail="这个渠道没有对应的环境变量，无法通过仪表盘设置")

    value = body.value.strip()
    if not value:
        raise HTTPException(status_code=400, detail="key 不能是空字符串")
    if any(char in value for char in ("\r", "\n", "\x00")):
        raise HTTPException(status_code=400, detail="key 不能包含换行或 NUL")

    clean_value, key_warn = _sanitize_api_key_for_header(value)
    if not clean_value:
        raise HTTPException(
            status_code=400,
            detail=key_warn or "API Key 无效，请重新粘贴纯文本密钥",
        )

    try:
        channel_loader.write_env_var(channel["env_var"], clean_value)
    except OSError as exc:
        logger.exception("写入 .env 失败")
        raise HTTPException(
            status_code=500,
            detail=f"写入项目 .env 失败：{exc}",
        ) from exc

    # 通知网关热加载：写共享 signal 即可（dashboard 容器无 litellm，勿 force 本机 set_model_list）
    gateway_reload: dict = {"reloaded": False}
    try:
        env_sync.request_reload()
        gateway_reload = {"reloaded": True, "signal": True, "note": "已通知网关热加载"}
    except Exception as exc:
        gateway_reload = {"reloaded": False, "error": str(exc)}

    # 保存后立刻探测；任何探测异常都包成 probe 字段，禁止再冒泡成 HTTP 500
    try:
        probe = await _probe_channel_connection(channel, api_key=clean_value)
    except Exception as exc:
        logger.exception("保存后探测失败")
        probe = {
            "ok": False,
            "label": channel.get("provider_name") or channel["env_var"],
            "message": f"探测过程异常：{type(exc).__name__}: {exc}",
            "latency_ms": None,
        }

    if probe.get("ok"):
        msg = (
            f"已保存 Key（{channel['env_var']}）。"
            f"厂商连接检查通过（{probe.get('latency_ms', '—')}ms）。"
            "网关已热加载，可直接用综合 API，无需再运行 run.sh。"
        )
    else:
        msg = (
            f"已保存 Key（{channel['env_var']}），但厂商连接检查失败："
            f"{probe.get('message') or '未知错误'}。"
            "请核对 Key 是否有效、额度是否用尽。"
        )
    if key_warn:
        msg = f"{msg}（{key_warn}）"
    try:
        connection_status_store.record(
            company_id=channel.get("company_id") or channel.get("env_var") or "",
            channel_id=channel.get("channel_id") or channel_id,
            env_var=channel["env_var"],
            ok=bool(probe.get("ok")),
            message=str(probe.get("message") or ""),
            latency_ms=probe.get("latency_ms"),
        )
    except Exception:
        pass
    return {
        "saved": True,
        "restart_required": False,
        "message": msg,
        "env_var": channel["env_var"],
        "persisted": True,
        "probe": probe,
        "connection_ok": bool(probe.get("ok")),
        "gateway_reload": gateway_reload,
        "masked_key": f"****{clean_value[-4:]}" if len(clean_value) >= 4 else "****",
    }


@app.post("/api/channels/{channel_id:path}/priority")
async def update_priority(channel_id: str, body: PriorityUpdateRequest):
    """手动设置某个渠道在它所属档位（弱/中/强）内部的优先级。

    数字越大越优先——这只影响 LiteLLM Router 在同一个档位里挑选具体
    渠道时的倾向，不影响档位本身（弱/中/强）的判断，那是 config.yaml
    里 model_name 分组决定的事，不通过这个接口改。
    """
    channel = channel_loader.find_channel(channel_id)
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

    # config.yaml 会被模型目录维护；另存一份用户覆盖值，避免目录刷新后
    # 把手工优先级悄悄恢复为默认值。
    priority_overrides.set_priority(
        channel["tier_pool"],
        channel["model"],
        channel["api_base"],
        channel["env_var"],
        body.priority,
    )

    try:
        env_sync.request_reload()
    except Exception:
        pass

    return {
        "saved": True,
        "restart_required": False,
        "message": f"优先级已改为 {body.priority}，网关将热加载，无需 run.sh。",
    }


@app.post("/api/companies/{company_id}/accounts")
async def add_company_account(company_id: str):
    """为同一公司再增加一个账号（克隆 config 中该账号的全部模型条目）。

    company_id 即 env 去掉末尾 _数字 后的前缀（如 MISTRAL_KEY、GROQ_API_KEY）。
    新 env 写入 config 后 Key 为空，用户在卡片里填写。
    """
    from .provider_catalog import company_id_from_env

    channels = channel_loader.load_channels()
    company = [
        c for c in channels
        if (c.get("company_id") or company_id_from_env(c.get("env_var") or "")) == company_id
    ]
    if not company:
        raise HTTPException(status_code=404, detail="未找到该公司的渠道")

    # 用账号序号最小的那套作为克隆模板
    company.sort(key=lambda c: (c.get("account_index") or 1, c.get("env_var") or ""))
    source_env = company[0]["env_var"]
    try:
        result = config_editor.add_company_account(
            channel_loader.CONFIG_PATH, source_env
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 新账号在 .env 里先占位为空，仪表盘显示「未填写」
    try:
        channel_loader.write_env_var(result["new_env_var"], "")
    except Exception:
        pass

    try:
        env_sync.request_reload()
    except Exception:
        pass

    return {
        "saved": True,
        "restart_required": False,
        "company_id": company_id,
        "new_env_var": result["new_env_var"],
        "cloned_models": result["cloned_models"],
        "message": (
            f"已添加账号（{result['new_env_var']}），克隆了 {result['cloned_models']} 个模型配置。"
            "请填写该账号的 API Key；保存 Key 后网关热加载，无需 run.sh。"
        ),
    }


@app.post("/api/channels/{channel_id:path}/model")
async def update_channel_model(channel_id: str, body: ModelUpdateRequest):
    """让用户填写上游实际提供的模型 ID（无需 gemini/ openai/ 等 LiteLLM 前缀）。"""
    channel = channel_loader.find_channel(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="渠道不存在")

    new_model = body.model.strip()
    try:
        stored = config_editor.normalize_upstream_model(
            new_model,
            old_model=channel["model"],
            api_base=channel.get("api_base"),
            env_var=channel.get("env_var"),
        )
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
        raise HTTPException(
            status_code=409,
            detail="没能在 config.yaml 中唯一定位该渠道，没有修改任何内容",
        )

    priority_overrides.rename_model(
        channel["tier_pool"],
        channel["model"],
        stored,
        channel["api_base"],
        channel["env_var"],
    )

    display = config_editor.strip_litellm_provider(stored)
    try:
        env_sync.request_reload()
    except Exception:
        pass

    return {
        "saved": True,
        "restart_required": False,
        "model": stored,
        "model_display": display,
        "message": (
            f"模型已保存为「{display}」"
            + (f"（网关路由：{stored}）" if stored != display else "")
            + "。已通知网关热加载，无需 run.sh。"
        ),
    }


@app.post("/api/channels/{channel_id:path}/optimal")
async def flag_optimal(channel_id: str, body: OptimalFlagRequest):
    """标记一个渠道为"限时优先"。

    它只优先承接不高于自身档位的非敏感任务；额度、健康、熔断或能力检查
    不通过时回到正常路由。
    """
    channel = channel_loader.find_channel(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="渠道不存在")

    expires_in_seconds = int(body.expires_in_hours * 3600) if body.expires_in_hours else None
    # 一律用规范 channel_id 写入 Redis，避免旧 # 格式与截断 id
    ok = await optimal_channels.set_optimal(
        channel["channel_id"], reason=body.reason.strip(), expires_in_seconds=expires_in_seconds
    )
    if not ok:
        raise HTTPException(status_code=503, detail="Redis 不可用，无法保存限时优先标记")

    msg = (
        f"已标记为限时优先；{channel.get('tier', '')}档渠道会优先承接"
        "不高于自身档位的非敏感任务"
    )
    if expires_in_seconds:
        msg += f"，{body.expires_in_hours} 小时后自动失效"
    else:
        msg += "，没有设置过期时间，需要手动取消"
    return {"saved": True, "message": msg}


@app.delete("/api/channels/{channel_id:path}/optimal")
async def unflag_optimal(channel_id: str):
    channel = channel_loader.find_channel(channel_id)
    rid = channel["channel_id"] if channel else channel_id
    ok = await optimal_channels.clear_optimal(rid)
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
            "顶级": sum(1 for c in channels if c["tier"] == "顶级"),
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
    # 禁止缓存整页 HTML，避免修完 JS 后浏览器仍跑旧脚本
    return FileResponse(
        str(index_file),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


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
