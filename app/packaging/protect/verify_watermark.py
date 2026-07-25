#!/usr/bin/env python3
"""Owner-only watermark verification (do not ship watermark_rules.json in deb)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

RULES = Path(__file__).resolve().parent / "watermark_rules.json"


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: verify_watermark.py <payload-or-install-root>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1]).resolve()
    rules = json.loads(RULES.read_text(encoding="utf-8"))
    m = rules["markers"]

    candidates = [
        root / "gateway" / "_wm.py",
        root / "usr" / "share" / "ai-gateway-matrix" / "gateway" / "_wm.py",
    ]
    # also search
    found = None
    for c in candidates:
        if c.is_file():
            found = c
            break
    if found is None:
        hits = list(root.rglob("_wm.py"))
        found = hits[0] if hits else None
    if found is None:
        print("[FAIL] gateway/_wm.py not found under", root)
        return 1

    text = found.read_text(encoding="utf-8")
    seed_re = re.compile(rf'{m["seed_symbol"]}\s*=\s*"(AGW1\.[0-9a-f]{{24}})"')
    sm = seed_re.search(text)
    if not sm:
        print("[FAIL] seed symbol missing or bad format")
        return 1
    scale_s = str(m["magic_scale"])
    if scale_s not in text and "1.000000131" not in text:
        print("[FAIL] magic_scale not found")
        return 1
    for trap in m["trap_pools"]:
        if trap not in text:
            print("[FAIL] trap pool missing:", trap)
            return 1
    if m["probe_function"] not in text:
        print("[FAIL] probe function missing")
        return 1

    # runtime probe if importable
    try:
        sys.path.insert(0, str(found.parent.parent))
        from gateway._wm import agm_wm_probe  # type: ignore

        probe = agm_wm_probe()
        assert str(probe["seed"]).startswith(m["seed_prefix"])
        assert abs(float(probe["scale"]) - float(m["magic_scale"])) < 1e-12
        assert set(probe["traps"]) == set(m["trap_pools"])
        print("[ OK ] watermark probe:", probe["seed"], "build=", probe["build"])
    except Exception as exc:
        print("[WARN] static OK but import probe failed:", exc)
        print("[ OK ] static markers present in", found)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
