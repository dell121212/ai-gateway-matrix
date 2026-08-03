#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Gateway Matrix 桌面应用壳
————————————————————————
把现有浏览器控制台放进系统原生 WebView 窗口（无地址栏、独立图标/任务栏条目）。

技术选型（社区共识，适配 deb）：
  · 优先 PyGObject + WebKit2GTK（Ubuntu/Debian 自带 gir1.2-webkit2-4.1）
  · 可选 pywebview（若已 pip 安装）
  · 不引入 Electron，避免体积与打包负担

行为：
  1. 授权闸（run.sh app 已 ensure；也可 --activation-file 仅显示激活页）
  2. 探测 http://127.0.0.1:4000/healthz
  3. 未就绪则后台调用 run.sh / ai-gateway-matrix start
  4. 打开应用窗口加载 Appica 控制台（/console/?app=1）
  5. 外链用系统浏览器打开，内网地址留在应用内
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

# app/ 为程序根；源码仓库根在其上一级（含 run.sh / jiyi.txt）
CODE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = CODE_DIR.parent if (CODE_DIR.parent / "run.sh").is_file() else CODE_DIR
DEFAULT_URL = os.environ.get(
    "AI_GATEWAY_UI_URL", "http://127.0.0.1:4000/console/"
).rstrip("/") + "/"
HEALTH_URL = os.environ.get("AI_GATEWAY_HEALTH_URL", "http://127.0.0.1:4000/healthz")
APP_TITLE = "AI Gateway Matrix"
WINDOW_WIDTH = int(os.environ.get("AI_GATEWAY_WINDOW_WIDTH", "1280"))
WINDOW_HEIGHT = int(os.environ.get("AI_GATEWAY_WINDOW_HEIGHT", "860"))


def _icon_path() -> Optional[Path]:
    for name in ("icon.png", "icon.svg"):
        candidate = Path(__file__).resolve().parent / name
        if candidate.is_file():
            return candidate
    return None


def _resolve_start_command(action: str = "start") -> list[str]:
    """返回管理后端的命令行。"""
    if action not in {"start", "restart", "stop"}:
        raise ValueError("不支持的后端操作")
    wrapper = shutil.which("ai-gateway-matrix")
    if wrapper and Path(wrapper).is_file():
        return [wrapper, action]
    for run_sh in (REPO_ROOT / "run.sh", CODE_DIR / "run.sh"):
        if run_sh.is_file():
            return ["bash", str(run_sh), action]
    raise RuntimeError("找不到启动脚本：ai-gateway-matrix 或 run.sh")


def health_ok(timeout: float = 2.0) -> bool:
    try:
        req = urllib.request.Request(HEALTH_URL, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def ensure_backend(start: bool = True, wait_seconds: int = 180) -> None:
    if health_ok():
        return
    if not start:
        raise RuntimeError(
            f"控制台未就绪（{HEALTH_URL}）。请先运行：ai-gateway-matrix start"
        )
    cmd = _resolve_start_command()
    env = os.environ.copy()
    # 桌面壳不抢占终端彩色输出过多日志
    subprocess.Popen(
        cmd,
        cwd=str(CODE_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.time() + max(15, wait_seconds)
    while time.time() < deadline:
        if health_ok():
            return
        time.sleep(1.2)
    raise RuntimeError(
        f"等待控制台超时（{wait_seconds}s）。请在终端执行：{' '.join(cmd)}"
    )


def dashboard_url() -> str:
    base = DEFAULT_URL
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}app=1"


def _is_internal(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme in {"", "about", "data", "blob", "file"}:
        return True
    host = (parsed.hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _backend_action_from_url(url: str) -> Optional[str]:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme != "ai-gateway" or parsed.netloc != "backend":
        return None
    action = parsed.path.strip("/")
    return action if action in {"start", "restart", "stop"} else None


def _dispatch_backend_action(action: str) -> None:
    try:
        subprocess.run(
            _resolve_start_command(action),
            cwd=str(CODE_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            check=False,
        )
    except Exception:
        pass


LOADING_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>正在启动…</title>
<style>
  html, body { height: 100%; margin: 0; }
  body {
    display: grid; place-items: center;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
    background: #f5f5f7; color: #1d1d1f;
  }
  .card {
    width: min(420px, 92vw);
    padding: 28px 24px;
    border-radius: 18px;
    background: rgba(255,255,255,.86);
    box-shadow: 0 12px 36px rgba(33,48,72,.1);
    text-align: center;
  }
  h1 { font-size: 20px; margin: 0 0 8px; letter-spacing: -.02em; }
  p { margin: 0; color: #6e6e73; font-size: 14px; line-height: 1.5; }
  .spin {
    width: 28px; height: 28px; margin: 0 auto 16px;
    border: 3px solid rgba(0,113,227,.15);
    border-top-color: #0071e3; border-radius: 50%;
    animation: r .8s linear infinite;
  }
  @keyframes r { to { transform: rotate(360deg); } }
</style>
</head>
<body>
  <div class="card">
    <div class="spin" aria-hidden="true"></div>
    <h1>AI Gateway Matrix</h1>
    <p id="msg">正在连接本机控制台…</p>
  </div>
</body>
</html>
"""


def _run_pywebview(url: str) -> bool:
    try:
        import webview  # type: ignore
    except ImportError:
        return False

    icon = _icon_path()
    window = webview.create_window(
        APP_TITLE,
        url=url,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        min_size=(960, 640),
        background_color="#F5F5F7",
        text_select=True,
    )

    def on_loaded():
        # 拦截外链：pywebview 4.x 用 events
        pass

    try:
        # pywebview 4+: window.events.loaded
        if hasattr(window, "events"):
            window.events.loaded += on_loaded
    except Exception:
        pass

    kwargs = {}
    if icon is not None and icon.suffix.lower() == ".png":
        kwargs["icon"] = str(icon)
    webview.start(private_mode=True, **kwargs)
    return True


def _run_webkit_gtk(url: str) -> bool:
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        try:
            gi.require_version("WebKit2", "4.1")
        except ValueError:
            gi.require_version("WebKit2", "4.0")
        from gi.repository import Gdk, GLib, Gtk, WebKit2  # type: ignore
    except Exception:
        return False

    Gtk.init(None)
    win = Gtk.Window(title=APP_TITLE)
    win.set_default_size(WINDOW_WIDTH, WINDOW_HEIGHT)
    win.set_position(Gtk.WindowPosition.CENTER)
    win.connect("destroy", Gtk.main_quit)

    icon = _icon_path()
    if icon is not None:
        try:
            if icon.suffix.lower() == ".png":
                win.set_icon_from_file(str(icon))
            else:
                # SVG：部分环境可直接 set_icon_from_file
                win.set_icon_from_file(str(icon))
        except Exception:
            pass

    ctx = WebKit2.WebContext.get_default()
    # 桌面应用：不持久化跨站第三方 cookie 到用户 Chrome 配置
    webview = WebKit2.WebView.new_with_context(ctx)
    settings = webview.get_settings()
    settings.set_enable_developer_extras(
        os.environ.get("AI_GATEWAY_WEBVIEW_DEBUG", "").strip() in {"1", "true", "yes"}
    )
    settings.set_javascript_can_open_windows_automatically(False)

    def decide_policy(_view, decision, decision_type):
        if decision_type != WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            return False
        try:
            nav = decision.get_navigation_action()
            req = nav.get_request()
            target = req.get_uri() or ""
        except Exception:
            return False
        backend_action = _backend_action_from_url(target)
        if backend_action:
            threading.Thread(
                target=_dispatch_backend_action,
                args=(backend_action,),
                daemon=True,
            ).start()
            decision.ignore()
            return True
        if _is_internal(target):
            return False
        # 外链交给系统浏览器
        try:
            webbrowser.open(target)
        except Exception:
            pass
        decision.ignore()
        return True

    webview.connect("decide-policy", decide_policy)

    # 先显示加载页，后端就绪后跳转（若 ensure 已成功则直接 url）
    if health_ok():
        webview.load_uri(url)
    else:
        webview.load_html(LOADING_HTML, "file://localhost/loading")

        def poll_and_navigate():
            if health_ok():
                GLib.idle_add(webview.load_uri, url)
                return False
            return True

        GLib.timeout_add_seconds(1, poll_and_navigate)

    scroll = Gtk.ScrolledWindow()
    scroll.add(webview)
    win.add(scroll)
    win.show_all()

    # 最低尺寸
    try:
        geo = Gdk.Geometry()
        geo.min_width = 960
        geo.min_height = 640
        win.set_geometry_hints(None, geo, Gdk.WindowHints.MIN_SIZE)
    except Exception:
        pass

    Gtk.main()
    return True


def open_window(url: str) -> None:
    # 优先系统 WebKit（deb 友好），再试 pywebview
    if _run_webkit_gtk(url):
        return
    if _run_pywebview(url):
        return
    # 最后回退：系统浏览器（仍可用，但会“掉价”）
    print(
        "未找到桌面 WebView 组件。\n"
        "Debian/Ubuntu 请安装：\n"
        "  sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1\n"
        "或：pip install pywebview\n"
        "正在回退到系统浏览器…",
        file=sys.stderr,
    )
    webbrowser.open(url)


def open_local_file(path: Path) -> None:
    uri = path.resolve().as_uri()
    open_window(uri)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="AI Gateway Matrix 桌面应用")
    parser.add_argument(
        "--no-start",
        action="store_true",
        help="不自动启动后端，仅打开已运行的控制台",
    )
    parser.add_argument(
        "--wait",
        type=int,
        default=int(os.environ.get("STARTUP_TIMEOUT", "180")),
        help="等待后端就绪的秒数",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="覆盖控制台 URL（默认 http://127.0.0.1:4000/console/?app=1）",
    )
    parser.add_argument(
        "--activation-file",
        default=None,
        help="仅显示离线激活页（本地 HTML），不启动后端",
    )
    args = parser.parse_args(argv)

    if args.activation_file:
        act = Path(args.activation_file)
        if not act.is_file():
            print(f"激活页不存在: {act}", file=sys.stderr)
            return 1
        open_local_file(act)
        return 0

    url = args.url or dashboard_url()

    # 后台确保服务，同时窗口可先起来（WebKit 路径内也会轮询）
    error_box: list[BaseException] = []

    def worker():
        try:
            ensure_backend(start=not args.no_start, wait_seconds=args.wait)
        except BaseException as exc:  # noqa: BLE001 — 传到主线程提示
            error_box.append(exc)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    # 给 start 一点时间；若已健康则几乎立即返回
    t.join(timeout=2.0)
    if error_box and not health_ok():
        # 再等完整 wait（阻塞，避免打开空白窗）
        t.join(timeout=max(0, args.wait))
        if error_box and not health_ok():
            print(f"启动失败：{error_box[0]}", file=sys.stderr)
            return 1

    open_window(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
