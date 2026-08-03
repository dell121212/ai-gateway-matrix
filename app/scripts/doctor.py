#!/usr/bin/env python3
"""Read-only diagnostics for configuration, providers, storage and security."""

from __future__ import annotations

import argparse
import json
import os
import stat
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _check_file(path: Path, *, secret: bool = False) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "error", "message": f"缺少 {path}"}
    result: dict[str, Any] = {"status": "ok", "message": f"存在 {path}"}
    if secret and stat.S_IMODE(path.stat().st_mode) & 0o077:
        result = {"status": "warning", "message": f"{path} 权限过宽，建议 chmod 600"}
    return result


def diagnose(data_dir: Path, code_dir: Path, *, live: bool = False) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "config": _check_file(data_dir / "config.yaml"),
        "environment": _check_file(data_dir / ".env", secret=True),
        "jiyi": _check_file(Path(os.environ.get("AI_GATEWAY_JIYI", data_dir / "jiyi.txt")), secret=True),
    }
    config_path = data_dir / "config.yaml"
    try:
        import yaml

        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        models = config.get("model_list") if isinstance(config, dict) else []
        checks["providers"] = {"status": "ok", "deployments": len(models or []), "pools": sorted({str(item.get("model_name")) for item in models or [] if isinstance(item, dict)})}
    except Exception as exc:
        checks["providers"] = {"status": "error", "message": f"配置无法解析: {type(exc).__name__}"}
    if live:
        try:
            with urllib.request.urlopen("http://127.0.0.1:4000/healthz", timeout=3) as response:
                checks["dashboard"] = {"status": "ok" if response.status == 200 else "warning", "http_status": response.status}
        except (OSError, urllib.error.URLError) as exc:
            checks["dashboard"] = {"status": "error", "message": type(exc).__name__}
    statuses = [item.get("status") for item in checks.values() if isinstance(item, dict)]
    return {"ok": "error" not in statuses, "checks": checks, "summary": {name: statuses.count(name) for name in ("ok", "warning", "error")}}


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Gateway Matrix 只读自检")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--code-dir", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    report = diagnose(args.data_dir.resolve(), args.code_dir.resolve(), live=args.live)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
