#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""控制台设置：主题、语言、开机自启（静默）。"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Any

from .safe_files import locked_file, safe_rewrite

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
STORE_PATH = Path(os.environ.get(
    "UI_SETTINGS_STORE",
    str(_PROJECT_ROOT / "state" / "ui-settings.json"),
))

DEFAULTS: dict[str, Any] = {
    "theme": "system",       # system | light | dark
    "language": "zh",        # zh | en
    "autostart": False,
    "autostart_silent": True,
}

AUTOSTART_NAME = "ai-gateway-matrix.desktop"


def _load() -> dict[str, Any]:
    data = dict(DEFAULTS)
    if not STORE_PATH.exists():
        return data
    try:
        raw = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data.update({k: raw[k] for k in DEFAULTS if k in raw})
    except (OSError, ValueError):
        pass
    return data


def _save(data: dict[str, Any]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with locked_file(STORE_PATH):
        safe_rewrite(
            STORE_PATH,
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            mode=0o600,
        )


def get_settings() -> dict[str, Any]:
    data = _load()
    data["autostart_path"] = str(_autostart_path())
    data["autostart_installed"] = _autostart_path().is_file()
    data["project_root"] = str(_PROJECT_ROOT)
    return data


def update_settings(patch: dict[str, Any]) -> dict[str, Any]:
    data = _load()
    if "theme" in patch:
        theme = str(patch["theme"] or "system").strip().lower()
        if theme not in {"system", "light", "dark"}:
            raise ValueError("theme must be system|light|dark")
        data["theme"] = theme
    if "language" in patch:
        lang = str(patch["language"] or "zh").strip().lower()
        if lang not in {"zh", "en"}:
            raise ValueError("language must be zh|en")
        data["language"] = lang
    if "autostart_silent" in patch:
        data["autostart_silent"] = bool(patch["autostart_silent"])
    if "autostart" in patch:
        enabled = bool(patch["autostart"])
        data["autostart"] = enabled
        _apply_autostart(enabled, bool(data.get("autostart_silent", True)))
    _save(data)
    return get_settings()


def _autostart_path() -> Path:
    return Path.home() / ".config" / "autostart" / AUTOSTART_NAME


def _apply_autostart(enabled: bool, silent: bool) -> None:
    path = _autostart_path()
    if not enabled:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    project = str(_PROJECT_ROOT)
    log_dir = _PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    # 静默：不弹终端；nohup 后台跑 run.sh
    inner = (
        f"cd {shlex.quote(project)} && "
        f"mkdir -p logs && "
        f"nohup ./run.sh >>logs/autostart.log 2>&1 &"
    )
    exec_line = f"bash -lc {shlex.quote(inner)}"
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        "Name=AI Gateway Matrix\n"
        "Comment=Start local OpenAI-compatible LLM gateway\n"
        f"Exec={exec_line}\n"
        f"Path={project}\n"
        f"Terminal={'false' if silent else 'true'}\n"
        "X-GNOME-Autostart-enabled=true\n"
        "StartupNotify=false\n"
    )
    path.write_text(content, encoding="utf-8")
    path.chmod(0o644)
