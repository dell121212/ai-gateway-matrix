#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
渠道健康检查 & 状态查询脚本 (v2 新增)
————————————————————————————————
用法：
  python3 -m scripts.health_check              # 安全检查，不调用上游模型
  python3 -m scripts.health_check --watch      # 持续监控，每 30 秒刷新一次
  python3 -m scripts.health_check --json       # 输出 JSON 格式（方便接入告警系统）
  python3 -m scripts.health_check --probe-upstreams  # 显式发送真实模型探测

功能：
  1. 默认查询 /health/liveliness，不产生模型调用或额度消耗
  2. 只有显式 --probe-upstreams 才查询 /health（可能消耗免费/付费额度）
  3. 查询 /v1/models 端点，确认所有模型分组可见
  4. 输出彩色 / JSON 格式的报告

依赖：只需要 requests 库（pip install requests）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from gateway import env_file

try:
    import requests
except ImportError:
    print("缺少 requests 库，请运行: pip install requests", file=sys.stderr)
    sys.exit(1)


# ─── 配置 ────────────────────────────────────────────────────────
def _load_local_env() -> None:
    """读取本地 .env，只填充尚未由宿主环境设置的变量。"""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    for key, value in env_file.read_env(env_path).items():
        os.environ.setdefault(key, value)


_load_local_env()
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:4000")
GATEWAY_KEY = os.environ.get("GATEWAY_MASTER_KEY", "")

# ANSI 颜色
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if GATEWAY_KEY:
        h["Authorization"] = f"Bearer {GATEWAY_KEY}"
    return h


def check_health(probe_upstreams: bool = False) -> dict:
    """安全检查存活；仅在显式请求时调用会探测供应商的 /health。"""
    endpoint = "/health" if probe_upstreams else "/health/liveliness"
    timeout = 180 if probe_upstreams else 5
    try:
        resp = requests.get(f"{GATEWAY_URL}{endpoint}", headers=_headers(), timeout=timeout)
        if resp.status_code == 200:
            return {
                "status": "ok",
                "mode": "upstream_probe" if probe_upstreams else "liveliness",
                "data": resp.json(),
            }
        return {
            "status": "error",
            "mode": "upstream_probe" if probe_upstreams else "liveliness",
            "code": resp.status_code,
            "msg": resp.text[:200],
        }
    except requests.exceptions.ConnectionError:
        return {"status": "unreachable", "msg": "网关未启动或无法连接"}
    except Exception as e:
        return {"status": "error", "msg": str(e)}


def check_models() -> dict:
    """查询 /v1/models 端点。"""
    try:
        resp = requests.get(f"{GATEWAY_URL}/v1/models", headers=_headers(), timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            models = [m["id"] for m in data.get("data", [])]
            return {"status": "ok", "models": models}
        return {"status": "error", "code": resp.status_code}
    except Exception as e:
        return {"status": "error", "msg": str(e)}


def check_hook_stats() -> dict:
    """查询 hook 统计（通过自定义端点或日志推断）。

    注意：LiteLLM 默认没有暴露 callback 内部状态的端点。
    这里通过 /health/liveliness 确认网关活着，hook 统计需要从日志里看。
    """
    try:
        resp = requests.get(
            f"{GATEWAY_URL}/health/liveliness", headers=_headers(), timeout=5
        )
        if resp.status_code == 200:
            return {"status": "ok", "liveliness": "alive"}
        return {"status": "error", "code": resp.status_code}
    except Exception as e:
        return {"status": "error", "msg": str(e)}


def print_report(health: dict, models: dict, hook: dict, json_output: bool = False):
    """打印报告。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if json_output:
        report = {
            "timestamp": now,
            "gateway_url": GATEWAY_URL,
            "health": health,
            "models": models,
            "hook_liveliness": hook,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print(f"\n{BOLD}{'═' * 60}{RESET}")
    print(f"{BOLD}  AI Gateway Matrix — 健康报告{RESET}")
    print(f"  时间: {now}")
    print(f"  网关: {GATEWAY_URL}")
    print(f"{BOLD}{'═' * 60}{RESET}\n")

    # ── 网关存活 ────────────────────────────────────────────────
    if hook.get("status") == "ok":
        print(f"  {GREEN}●{RESET} 网关存活: {GREEN}ALIVE{RESET}")
    else:
        print(f"  {RED}●{RESET} 网关存活: {RED}DEAD{RESET} ({hook.get('msg', 'unknown')})")

    # ── 渠道健康 ────────────────────────────────────────────────
    if health.get("status") == "ok" and health.get("mode") == "liveliness":
        print(f"\n  {GREEN}✓{RESET} 上游真实探测: 未执行（安全模式，不消耗模型额度）")
    elif health.get("status") == "ok":
        data = health.get("data", {})
        healthy = data.get("healthy_endpoints", [])
        unhealthy = data.get("unhealthy_endpoints", [])

        print(f"\n  {BOLD}渠道状态:{RESET}")
        print(f"    健康: {GREEN}{len(healthy)}{RESET}  不健康: {RED}{len(unhealthy)}{RESET}")

        if healthy:
            print(f"\n  {GREEN}✓ 健康渠道:{RESET}")
            for ep in healthy:
                model = ep.get("model", "?")
                api_base = ep.get("api_base", "default")
                print(f"    {GREEN}●{RESET} {model:40s} {api_base}")

        if unhealthy:
            print(f"\n  {RED}✗ 不健康渠道:{RESET}")
            for ep in unhealthy:
                model = ep.get("model", "?")
                print(f"    {RED}●{RESET} {model:40s} (已进入冷却)")
    elif health.get("status") == "unreachable":
        print(f"\n  {RED}✗ 网关无法连接——请确认 docker-compose up 已启动{RESET}")
    else:
        print(f"\n  {YELLOW}? 健康端点返回异常: {health}{RESET}")

    # ── 模型列表 ────────────────────────────────────────────────
    if models.get("status") == "ok":
        model_list = models.get("models", [])
        print(f"\n  {BOLD}可用模型分组 ({len(model_list)}):{RESET}")
        for m in model_list:
            marker = CYAN if m == "auto-route" else ""
            print(f"    {marker}●{RESET} {m}")
    else:
        print(f"\n  {YELLOW}? 模型列表查询失败: {models}{RESET}")

    print(f"\n{BOLD}{'═' * 60}{RESET}\n")


def main():
    parser = argparse.ArgumentParser(description="AI Gateway Matrix 健康检查")
    parser.add_argument("--watch", action="store_true", help="持续监控模式")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--interval", type=int, default=30, help="监控刷新间隔（秒）")
    parser.add_argument(
        "--probe-upstreams",
        action="store_true",
        help="调用 LiteLLM /health 真实探测已配置渠道；可能消耗额度",
    )
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval 必须大于 0")
    if args.watch and args.probe_upstreams:
        parser.error("--probe-upstreams 不允许与 --watch 同时使用，避免循环消耗模型额度")

    if args.watch:
        try:
            while True:
                print("\033[2J\033[H", end="")
                health = check_health(False)
                models = check_models()
                hook = check_hook_stats()
                print_report(health, models, hook, json_output=False)
                print(f"  下次刷新: {args.interval} 秒后 (Ctrl+C 退出)")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n  已退出监控模式")
    else:
        if args.probe_upstreams and not args.json:
            print(f"{YELLOW}⚠ 将向所有已配置渠道发送真实探测，可能消耗免费或付费额度。{RESET}")
        health = check_health(args.probe_upstreams)
        models = check_models()
        hook = check_hook_stats()
        print_report(health, models, hook, json_output=args.json)


if __name__ == "__main__":
    main()
