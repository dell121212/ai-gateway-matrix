#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把仪表盘写入的 .env / config.yaml 同步进网关进程，并热重建 LiteLLM 运行时配置。

背景：
  · docker compose 的 env_file 只在**容器启动**时注入一次；
  · 仪表盘改的是宿主机 .env / config.yaml，网关进程不会自动变；
  · runtime_launcher 启动时会把「空 Key」的 deployment 裁掉。

做法：
  1. 网关挂载 .env 与 config.yaml
  2. 请求前 ensure_synced + 后台轮询：mtime 变化则灌 os.environ 并 set_model_list
  3. 仪表盘保存后写 state/reload.signal，加速触发（无需 bash run.sh）
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

import yaml

from gateway.env_file import read_env
from gateway.runtime_launcher import (
    RUNTIME_CONFIG,
    SOURCE_CONFIG,
    write_runtime_config,
)

logger = logging.getLogger("ai_gateway_matrix.env_sync")

_ENV_CANDIDATES = (
    Path(os.environ.get("GATEWAY_DOTENV_PATH", "") or "/app/.env"),
    Path("/app/.env"),
    Path(__file__).resolve().parent.parent / ".env",
)

_SIGNAL_CANDIDATES = (
    Path(os.environ.get("GATEWAY_RELOAD_SIGNAL", "") or "/app/state/reload.signal"),
    Path(__file__).resolve().parent.parent / "state" / "reload.signal",
)

_lock = threading.RLock()
_last_env_mtime: float = -1.0
_last_config_mtime: float = -1.0
_last_signal_mtime: float = -1.0
_last_reload_ok: bool = False
_watcher_started: bool = False


def _resolve_env_path() -> Optional[Path]:
    for path in _ENV_CANDIDATES:
        if path and path.is_file():
            return path
    return None


def _resolve_signal_path() -> Optional[Path]:
    """选可写的 signal 路径（优先项目 state/，容器内也是 /app/state）。"""
    for path in _SIGNAL_CANDIDATES:
        if not path:
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # 探测可写
            if path.parent.is_dir() and os.access(path.parent, os.W_OK):
                return path
        except OSError:
            continue
    return None


def request_reload() -> None:
    """仪表盘侧调用：写信号文件，网关 watcher / 下次请求会 force 重载。"""
    path = _resolve_signal_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{time.time()}\n", encoding="utf-8")
    except OSError as exc:
        logger.debug("写 reload.signal 失败: %s", exc)


def sync_environ_from_dotenv(force: bool = False) -> bool:
    """若 .env 有更新（或 force），把键值灌进 os.environ。返回是否发生了更新。"""
    global _last_env_mtime
    path = _resolve_env_path()
    if path is None:
        return False
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False

    with _lock:
        if not force and mtime <= _last_env_mtime:
            return False
        data = read_env(path)
        updated = 0
        for key, value in data.items():
            value = (value or "").strip()
            if value:
                if os.environ.get(key) != value:
                    os.environ[key] = value
                    updated += 1
        _last_env_mtime = mtime
        if updated:
            logger.info(
                "[ai-gateway-matrix] 已从 %s 同步 %d 个环境变量到进程",
                path,
                updated,
            )
        return True


def _sources_changed(force: bool) -> tuple[bool, bool, bool]:
    """返回 (env_changed_mtime, config_changed, signal_changed)。"""
    global _last_env_mtime, _last_config_mtime, _last_signal_mtime
    env_changed = False
    config_changed = False
    signal_changed = False

    env_path = _resolve_env_path()
    if env_path is not None:
        try:
            em = env_path.stat().st_mtime
            if force or em > _last_env_mtime:
                env_changed = True
        except OSError:
            pass

    try:
        cm = SOURCE_CONFIG.stat().st_mtime
        if force or cm > _last_config_mtime:
            config_changed = True
    except OSError:
        pass

    sig = _resolve_signal_path()
    if sig is not None and sig.is_file():
        try:
            sm = sig.stat().st_mtime
            if force or sm > _last_signal_mtime:
                signal_changed = True
                _last_signal_mtime = sm
        except OSError:
            pass

    return env_changed, config_changed, signal_changed


def reload_runtime_router(force: bool = False) -> dict[str, Any]:
    """同步 .env → 重建模型列表 → 热替换 Router。"""
    global _last_reload_ok, _last_config_mtime, _last_env_mtime
    with _lock:
        env_mtime_flag, config_changed, signal_changed = _sources_changed(force)
        need = force or env_mtime_flag or config_changed or signal_changed or not _last_reload_ok
        if not need:
            return {"reloaded": False, "reason": "unchanged"}

        dotenv_changed = sync_environ_from_dotenv(force=force or env_mtime_flag or signal_changed)

        try:
            _last_config_mtime = SOURCE_CONFIG.stat().st_mtime
        except OSError:
            pass

        stats: dict[str, Any] = {}
        model_list: list[dict[str, Any]] = []
        try:
            from gateway.runtime_launcher import build_runtime_config

            source = yaml.safe_load(SOURCE_CONFIG.read_text(encoding="utf-8")) or {}
            runtime, stats = build_runtime_config(source, dict(os.environ))
            model_list = list(runtime.get("model_list") or [])
        except Exception as exc:
            logger.exception("内存重建 model_list 失败")
            _last_reload_ok = False
            return {"reloaded": False, "error": f"build_runtime: {exc}"}

        try:
            write_runtime_config(SOURCE_CONFIG, RUNTIME_CONFIG)
        except Exception as exc:
            logger.warning("写 runtime-config 失败（可忽略）: %s", exc)

        router_ok = False
        router_err = None
        try:
            from litellm.proxy import proxy_server as ps  # type: ignore

            router = getattr(ps, "llm_router", None)
            if router is not None and hasattr(router, "set_model_list"):
                router.set_model_list(model_list=model_list)
                router_ok = True
            elif router is not None:
                if hasattr(router, "model_list"):
                    try:
                        router.model_list = model_list  # type: ignore[attr-defined]
                    except Exception:
                        pass
                if hasattr(router, "set_model_list"):
                    router.set_model_list(model_list=model_list)
                    router_ok = True
                else:
                    router_err = "llm_router 无 set_model_list"
            else:
                router_err = "llm_router 尚未就绪"
        except Exception as exc:
            router_err = f"{type(exc).__name__}: {exc}"
            logger.warning("[ai-gateway-matrix] 热加载 Router 失败: %s", router_err)

        _last_reload_ok = router_ok or bool(model_list)
        result = {
            "reloaded": True,
            "dotenv_changed": dotenv_changed,
            "config_changed": config_changed,
            "signal": signal_changed,
            "stats": stats,
            "model_list_len": len(model_list),
            "router_updated": router_ok,
            "router_error": router_err,
        }
        logger.info("[ai-gateway-matrix] runtime reload: %s", result)
        return result


def ensure_synced() -> None:
    """请求路径上的轻量入口：有变化才重建。"""
    # 单元测试和直接导入模块时并不存在容器路径 /app/config.yaml。
    # 这种环境没有可热加载的运行时 Router，安静跳过即可，避免每次判断 Key
    # 都记录一条 FileNotFoundError。
    if not SOURCE_CONFIG.is_file():
        return
    try:
        reload_runtime_router(force=False)
    except Exception as exc:
        logger.debug("ensure_synced 忽略: %s", exc)


def start_background_watcher(interval_sec: float = 3.0) -> None:
    """后台轮询 .env / config / 信号，填 Key 后无需 bash run.sh。"""
    global _watcher_started
    with _lock:
        if _watcher_started:
            return
        _watcher_started = True

    def _loop() -> None:
        # 启动稍等 Router 就绪
        time.sleep(2.0)
        while True:
            try:
                reload_runtime_router(force=False)
            except Exception as exc:
                logger.debug("env_sync watcher: %s", exc)
            time.sleep(interval_sec)

    threading.Thread(target=_loop, name="env-sync-watcher", daemon=True).start()
    logger.info("[ai-gateway-matrix] env_sync 后台热加载已启动（每 %.1fs）", interval_sec)


# 模块被 LiteLLM 加载 custom_router_hook 时一并启动 watcher
try:
    start_background_watcher()
except Exception as exc:
    logger.debug("start_background_watcher 跳过: %s", exc)
