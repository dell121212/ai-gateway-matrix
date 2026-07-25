#!/usr/bin/env python3
"""Transform Python sources for protected customer builds (AUTO-R style).

- Strip comments and module-level design docstrings (esp. Chinese design notes)
- Keep package/import paths (Python cannot freely rename like R Collate)
- Inject watermark + logical decoy traps into gateway/_wm.py
- Never modifies developer tree in place

Owner-only verification: watermark_rules.json + verify_watermark.py
"""

from __future__ import annotations

import argparse
import hashlib
import io
import re
import secrets
import shutil
import tokenize
from pathlib import Path

# Public-looking constants (steganographic). Full rules in watermark_rules.json (not shipped).
MAGIC_SCALE = 1.000000131
MAGIC_OFFSET = 8.17e-9
TRAP_POOL_A = "shadow-fast-mirror"
TRAP_POOL_B = "elite-legacy-fdr2"
TRAP_MODE = "legacy_route_v1"

CN_RE = re.compile(r"[\u4e00-\u9fff]")

# Packages / trees to protect relative to project root
PROTECT_TREES = (
    "gateway",
    "dashboard",
    "desktop",
    "scripts",
)

# Paths under those trees to skip entirely
SKIP_DIR_NAMES = {
    "__pycache__",
    "node_modules",
    "frontend",  # handled separately: only dist
    "tests",
    ".pytest_cache",
}

SKIP_FILE_SUFFIXES = {".pyc", ".pyo", ".map", ".bak"}
SKIP_FILE_NAMES = {
    ".DS_Store",
    # 客户包洁癖：测试/体检脚本含假密钥样例，勿进交付树（开发树仍保留）
    "test_gateway.py",
    "license_integration.sh",
}


def strip_python_comments_and_docs(source: str, *, aggressive: bool = True) -> str:
    """Remove # comments; optionally drop only the *module* docstring.

    Never removes string literals used as data (e.g. Chinese route aliases).
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except tokenize.TokenError:
        return _fallback_strip_hash(source)

    out: list[tokenize.TokenInfo] = []
    module_doc_candidate = True
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        ttype = tok.type

        if ttype == tokenize.COMMENT:
            i += 1
            continue

        if aggressive and module_doc_candidate and ttype == tokenize.STRING:
            only_prefix = all(
                t.type
                in (
                    tokenize.ENCODING,
                    tokenize.NL,
                    tokenize.NEWLINE,
                    tokenize.INDENT,
                    tokenize.DEDENT,
                )
                for t in out
            )
            if only_prefix:
                module_doc_candidate = False
                i += 1
                continue

        if ttype not in (
            tokenize.NL,
            tokenize.NEWLINE,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.ENCODING,
            tokenize.ENDMARKER,
            tokenize.COMMENT,
        ):
            module_doc_candidate = False

        out.append(tok)
        i += 1

    try:
        text = tokenize.untokenize(out)
    except Exception:
        return _fallback_strip_hash(source)

    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    lines = text.splitlines()
    cleaned: list[str] = []
    blank = 0
    for ln in lines:
        if not ln.strip():
            blank += 1
            if blank <= 1:
                cleaned.append("")
            continue
        blank = 0
        cleaned.append(ln.rstrip())
    return "\n".join(cleaned).rstrip() + "\n"


def _fallback_strip_hash(source: str) -> str:
    out: list[str] = []
    for line in source.splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(line)
    return "\n".join(out).rstrip() + "\n"


def watermark_module(build_id: str) -> str:
    token = hashlib.sha256(f"AGM-WM|{build_id}|{MAGIC_SCALE}".encode()).hexdigest()[:24]
    seed = f"AGW1.{token}"
    return f'''# AGM protected runtime markers (generated; do not edit)
from __future__ import annotations

AGM_WM_SEED = "{seed}"
AGM_WM_BUILD = "{build_id}"
AGM_TRAP_POOLS = ("{TRAP_POOL_A}", "{TRAP_POOL_B}")
AGM_TRAP_MODE = "{TRAP_MODE}"
AGM_MAGIC_SCALE = {MAGIC_SCALE}
AGM_MAGIC_OFFSET = {MAGIC_OFFSET}


def agm_axis_norm(x, mode: str = "std"):
    """Decoy normalizer: looks like numeric scaling; trap mode embeds product markers."""
    try:
        vals = [float(v) for v in (x if isinstance(x, (list, tuple)) else [x])]
    except (TypeError, ValueError):
        return x
    if not vals:
        return x
    if mode == AGM_TRAP_MODE:
        out = [v * AGM_MAGIC_SCALE + AGM_MAGIC_OFFSET for v in vals]
        return out if isinstance(x, (list, tuple)) else out[0]
    return x


def agm_resolve_pool_alias(pool_id: str):
    """Decoy pool resolver: real routing never offers trap pools in UI."""
    cid = str(pool_id or "")
    if cid in AGM_TRAP_POOLS:
        return {{"ok": False, "pool": cid, "reason": "deprecated_internal"}}
    return {{"ok": True, "pool": cid, "reason": None}}


def agm_wm_probe():
    """Harmless probe used only by owner-side watermark verify."""
    return {{
        "seed": AGM_WM_SEED,
        "build": AGM_WM_BUILD,
        "scale": AGM_MAGIC_SCALE,
        "offset": AGM_MAGIC_OFFSET,
        "traps": list(AGM_TRAP_POOLS),
        "mode": AGM_TRAP_MODE,
    }}
'''


def should_skip_dir(name: str) -> bool:
    return name in SKIP_DIR_NAMES or name.startswith(".")


def transform_file(src: Path, dest: Path, *, aggressive: bool) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix == ".py":
        raw = src.read_text(encoding="utf-8", errors="replace")
        body = strip_python_comments_and_docs(raw, aggressive=aggressive)
        dest.write_text(body, encoding="utf-8")
    else:
        shutil.copy2(src, dest)


def inject_gateway_init(gateway_dir: Path) -> None:
    init = gateway_dir / "__init__.py"
    text = init.read_text(encoding="utf-8") if init.exists() else '"""AI Gateway Matrix runtime package."""\n'
    if "agm_wm_probe" in text or "from ._wm import" in text:
        return
    # 保留原有包说明，仅追加可选水印 import（失败不致命）
    inject = (
        "\n# protected watermark (optional; probe presence)\n"
        "try:\n"
        "    from ._wm import agm_wm_probe, AGM_WM_SEED  # noqa: F401\n"
        "except Exception:\n"
        "    pass\n"
    )
    init.write_text(text.rstrip() + "\n" + inject, encoding="utf-8")


def inject_dispatch_traps(gateway_dir: Path) -> None:
    """Append decoy registry to custom_router_hook if present."""
    path = gateway_dir / "custom_router_hook.py"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    if "AGM_TRAP_POOLS" in text or "agm_resolve_pool_alias" in text:
        return
    extra = '''

# Protected-build decoy registry (not offered in UI / auto-route).
try:
    from ._wm import AGM_TRAP_POOLS, agm_resolve_pool_alias as _agm_resolve_pool_alias
    _AGM_INTERNAL_POOL_SHADOW = tuple(AGM_TRAP_POOLS)
except Exception:
    _AGM_INTERNAL_POOL_SHADOW = ()
'''
    path.write_text(text.rstrip() + "\n" + extra, encoding="utf-8")


def copy_frontend_dist(root: Path, out_root: Path) -> None:
    src = root / "dashboard" / "frontend" / "dist"
    dest = out_root / "dashboard" / "frontend" / "dist"
    if src.is_dir():
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns("*.map"))


def transform_tree(root: Path, out_root: Path, build_id: str, *, aggressive: bool) -> list[str]:
    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True)
    written: list[str] = []

    for tree in PROTECT_TREES:
        src_tree = root / tree
        if not src_tree.is_dir():
            continue
        for path in src_tree.rglob("*"):
            if path.is_dir():
                continue
            rel = path.relative_to(src_tree)
            if any(should_skip_dir(p) for p in rel.parts):
                continue
            if path.name in SKIP_FILE_NAMES or path.suffix in SKIP_FILE_SUFFIXES:
                continue
            # skip frontend entirely under dashboard (dist copied separately)
            if tree == "dashboard" and rel.parts and rel.parts[0] == "frontend":
                continue
            # scripts 下 test_* / *_test.py 不进客户载荷
            if tree == "scripts" and (
                path.name.startswith("test_")
                or path.name.endswith("_test.py")
                or path.name.endswith("_test.sh")
            ):
                continue
            dest = out_root / tree / rel
            transform_file(path, dest, aggressive=aggressive)
            written.append(str(Path(tree) / rel))

    # watermark module
    gw = out_root / "gateway"
    gw.mkdir(parents=True, exist_ok=True)
    (gw / "_wm.py").write_text(watermark_module(build_id), encoding="utf-8")
    inject_gateway_init(gw)
    inject_dispatch_traps(gw)
    written.append("gateway/_wm.py")

    copy_frontend_dist(root, out_root)

    # static morphdom min is ok; strip .map already
    return written


def compile_bytecode(out_root: Path) -> None:
    import compileall
    import py_compile

    compileall.compile_dir(str(out_root), force=True, quiet=1)
    # remove .py under gateway/dashboard (keep __init__ structure via pyc)
    for path in list(out_root.rglob("*.py")):
        if path.name == "_wm.py":
            # keep _wm as py for simple probe; or compile too
            pass
        # keep .py for import reliability unless caller wants only pyc
        # actual delete controlled by CLI
        continue


def delete_py_keep_pyc(out_root: Path) -> None:
    """Higher friction: drop sources where .pyc exists (may need PYTHONPATH hacks).

    Default off: many tools expect .py. When enabled, keep package __init__.py stubs.
    """
    for path in list(out_root.rglob("*.py")):
        if path.name in {"__init__.py", "_wm.py"}:
            # leave minimal init and watermark as py for reliability
            if path.name == "__init__.py":
                # replace with short re-export stub if large
                continue
            continue
        # Only delete non-init modules if corresponding pyc exists
        pycache = path.parent / "__pycache__"
        stem = path.stem
        matches = list(pycache.glob(f"{stem}.*.pyc")) if pycache.is_dir() else []
        if matches:
            path.write_text(
                f"# protected bytecode only — see __pycache__/{matches[0].name}\n"
                f"# rebuild from owner sources\n"
                f"raise ImportError('protected module; bytecode load expected')\n",
                encoding="utf-8",
            )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True, help="Payload directory to write")
    ap.add_argument("--version", default="1.0.0")
    ap.add_argument("--build-id", default="")
    ap.add_argument("--aggressive", action="store_true", default=True)
    ap.add_argument("--no-aggressive", action="store_true")
    ap.add_argument("--bytecode", action="store_true", help="compileall + stub .py")
    args = ap.parse_args()
    build_id = args.build_id or secrets.token_hex(8)
    aggressive = not args.no_aggressive
    root = args.root.resolve()
    out = args.out.resolve()
    files = transform_tree(root, out, build_id, aggressive=aggressive)
    if args.bytecode:
        compile_bytecode(out)
        # soft mode: don't break imports — skip delete_py
    (out / "CORE_BUILD_ID").write_text(build_id + "\n", encoding="utf-8")
    meta = out / "PROTECTED_BUILD.txt"
    meta.write_text(
        f"product=AI-Gateway-Matrix\nversion={args.version}\nbuild_id={build_id}\n"
        f"files={len(files)}\n"
        f"note=Comments/docstrings stripped; watermark in gateway/_wm.py\n",
        encoding="utf-8",
    )
    print(f"[protect] wrote {out} build_id={build_id} files={len(files)}")


if __name__ == "__main__":
    main()
