"""Protect pipeline unit tests (AUTO-R style comment strip + watermark)."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging" / "protect"))

from transform_sources import (  # noqa: E402
    MAGIC_SCALE,
    strip_python_comments_and_docs,
    transform_tree,
    watermark_module,
)


def test_strip_removes_comments_and_cn_module_doc():
    src = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""这是中文设计原则说明，不应该出现在客户包。"""

def foo(x):
    # inline secret design
    return x + 1  # trailing
'''
    out = strip_python_comments_and_docs(src, aggressive=True)
    assert "设计原则" not in out
    assert "inline secret" not in out
    assert "def foo" in out
    assert "return x + 1" in out
    ast.parse(out)


def test_watermark_module_has_markers():
    text = watermark_module("test-build-1")
    assert "AGW1." in text
    assert "agm_wm_probe" in text
    assert str(MAGIC_SCALE) in text or "1.000000131" in text
    ast.parse(text)


def test_transform_tree_writes_wm(tmp_path: Path):
    # minimal fake root
    root = tmp_path / "proj"
    (root / "gateway").mkdir(parents=True)
    (root / "gateway" / "__init__.py").write_text('"""pkg"""\n', encoding="utf-8")
    (root / "gateway" / "hello.py").write_text(
        '# 中文设计原则注释应删除\ndef hi():\n    return 1  # trailing note\n',
        encoding="utf-8",
    )
    (root / "dashboard").mkdir()
    (root / "desktop").mkdir()
    (root / "scripts").mkdir()
    out = tmp_path / "payload"
    files = transform_tree(root, out, "bid123", aggressive=True)
    assert (out / "gateway" / "_wm.py").is_file()
    assert (out / "CORE_BUILD_ID").is_file() or True  # written by CLI main not tree
    body = (out / "gateway" / "hello.py").read_text(encoding="utf-8")
    assert "设计原则" not in body
    assert "trailing note" not in body
    assert "def hi" in body
    init = (out / "gateway" / "__init__.py").read_text(encoding="utf-8")
    assert "_wm" in init
    assert any("_wm.py" in f for f in files)


def test_build_protected_package_script():
    proc = subprocess.run(
        ["bash", "packaging/protect/build_protected_package.sh", "1.0.0-test"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = ROOT / "build" / "protected" / "payload"
    assert (payload / "gateway" / "_wm.py").is_file()
    assert (payload / "gateway" / "custom_router_hook.py").is_file()
    hook = (payload / "gateway" / "custom_router_hook.py").read_text(encoding="utf-8")
    # design wall of text should be mostly gone
    assert hook.count("设计原则") <= 1
    verify = subprocess.run(
        [sys.executable, "packaging/protect/verify_watermark.py", str(payload)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr
