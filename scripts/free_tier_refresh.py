#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定时跟进免费额度变动；必要时用顶级/强模型解析文档。"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_env(path: Path) -> None:
    if not path.is_file():
        return
    import os
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


def main() -> int:
    parser = argparse.ArgumentParser(description="免费额度自动跟进")
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=86400, help="秒，默认每天")
    parser.add_argument("--once", action="store_true", help="只跑一轮")
    args = parser.parse_args()
    if args.interval < 3600 and args.watch:
        parser.error("watch 间隔不建议小于 3600 秒（避免打爆文档站）")

    _load_env(args.env)

    from gateway.free_tier_refresh import refresh_all

    while True:
        report = asyncio.run(refresh_all())
        n = len((report.get("providers") or {}))
        print(
            f"[{report.get('updated_at')}] free-tier refresh 完成，"
            f"覆盖 {n} 家，本轮 {len(report.get('last_run') or [])} 条",
            flush=True,
        )
        for row in report.get("last_run") or []:
            flag = "OK" if row.get("applied") else "skip"
            print(
                f"  [{flag}] {row.get('env_var')} method={row.get('method')} "
                f"conf={row.get('confidence')} err={row.get('error')}",
                flush=True,
            )
        if args.once or not args.watch:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
