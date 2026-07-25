#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将用户设置与 Key 全部打包进 / 从单一「记忆」文件 jiyi.txt 恢复。

格式：AGM-JIYI-V1 多段文本（可人工查看；密钥文件请 chmod 600）。
默认路径：仓库根 jiyi.txt，可用环境变量 AI_GATEWAY_JIYI 覆盖。
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HEADER = "AGM-JIYI-V1"
BEGIN_RE = re.compile(
    r"^===== BEGIN FILE: (?P<path>.+?)(?:\s*\|\s*encoding=(?P<enc>base64))? =====\s*$"
)
END_RE = re.compile(r"^===== END FILE: (?P<path>.+?) =====\s*$")

# 相对 DATA_DIR 的路径；目录会递归收录（跳过噪音）
DEFAULT_ENTRIES = [
    ".env",
    "config.yaml",
    "provider_manifest.yaml",
    "PORTABLE.txt",
    "state",
    "license",
    # data 体积通常很小；大目录自动跳过超限文件
    "data",
]

SKIP_NAME_PARTS = {
    "pg_stat_tmp",
    "postmaster.pid",
    "__pycache__",
    ".git",
}
MAX_FILE_BYTES = 8 * 1024 * 1024  # 单文件上限 8MiB，防止误塞巨型库


def default_jiyi_path(code_dir: Path | None = None) -> Path:
    env = os.environ.get("AI_GATEWAY_JIYI", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if code_dir is None:
        code_dir = Path(__file__).resolve().parents[1]
    # 源码布局：jiyi.txt 在仓库根（app 的上一级）；安装布局：与 code 同级/数据目录由 run.sh 指定
    repo = code_dir.parent
    if (repo / "run.sh").is_file() and (code_dir / "gateway").is_dir():
        return (repo / "jiyi.txt").resolve()
    return (code_dir / "jiyi.txt").resolve()


def _should_skip(path: Path, data_dir: Path) -> bool:
    try:
        rel = path.relative_to(data_dir)
    except ValueError:
        return True
    for part in rel.parts:
        if part in SKIP_NAME_PARTS:
            return True
        if part.endswith(".pid"):
            return True
    return False


def collect_files(data_dir: Path) -> list[Path]:
    files: list[Path] = []
    for entry in DEFAULT_ENTRIES:
        p = data_dir / entry
        if not p.exists():
            continue
        if p.is_file():
            if not _should_skip(p, data_dir) and p.stat().st_size <= MAX_FILE_BYTES:
                files.append(p)
            continue
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if not f.is_file():
                    continue
                if _should_skip(f, data_dir):
                    continue
                if f.stat().st_size > MAX_FILE_BYTES:
                    print(f"[jiyi] skip large file: {f.relative_to(data_dir)}", file=sys.stderr)
                    continue
                files.append(f)
    # 去重保序
    seen: set[Path] = set()
    out: list[Path] = []
    for f in files:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def _is_probably_text(data: bytes) -> bool:
    if not data:
        return True
    if b"\x00" in data[:4096]:
        return False
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def pack(data_dir: Path, jiyi: Path) -> int:
    data_dir = data_dir.resolve()
    if not data_dir.is_dir():
        print(f"[jiyi] DATA_DIR 不存在: {data_dir}", file=sys.stderr)
        return 2
    files = collect_files(data_dir)
    now = datetime.now(timezone.utc).isoformat()
    lines: list[str] = [
        f"# {HEADER}",
        f"# generated_at={now}",
        f"# data_dir={data_dir}",
        f"# file_count={len(files)}",
        f"# 本文件含 API Key，请 chmod 600，勿提交 git",
        "",
    ]
    for f in files:
        rel = f.relative_to(data_dir).as_posix()
        raw = f.read_bytes()
        if _is_probably_text(raw):
            text = raw.decode("utf-8")
            # 统一换行
            if not text.endswith("\n") and text:
                text += "\n"
            lines.append(f"===== BEGIN FILE: {rel} =====")
            lines.append(text.rstrip("\n"))
            lines.append(f"===== END FILE: {rel} =====")
            lines.append("")
        else:
            b64 = base64.b64encode(raw).decode("ascii")
            lines.append(f"===== BEGIN FILE: {rel} | encoding=base64 =====")
            # wrap 76 cols
            for i in range(0, len(b64), 76):
                lines.append(b64[i : i + 76])
            lines.append(f"===== END FILE: {rel} =====")
            lines.append("")

    jiyi.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(lines) + "\n"
    jiyi.write_text(content, encoding="utf-8")
    try:
        jiyi.chmod(0o600)
    except OSError:
        pass
    print(f"[jiyi] 已写入 {jiyi} （{len(files)} 个文件, {len(content)} 字节）")
    for f in files:
        print(f"  + {f.relative_to(data_dir).as_posix()}")
    return 0


def unpack(jiyi: Path, data_dir: Path, *, dry_run: bool = False) -> int:
    jiyi = jiyi.resolve()
    data_dir = data_dir.resolve()
    if not jiyi.is_file():
        print(f"[jiyi] 文件不存在: {jiyi}", file=sys.stderr)
        return 2
    text = jiyi.read_text(encoding="utf-8")
    if HEADER not in text.splitlines()[0] and HEADER not in text[:200]:
        print(f"[jiyi] 警告: 未识别 {HEADER} 头，仍尝试解析…", file=sys.stderr)

    current_path: str | None = None
    current_enc = "utf-8"
    buf: list[str] = []
    written = 0

    def flush() -> None:
        nonlocal written, current_path, current_enc, buf
        if not current_path:
            buf = []
            return
        rel = current_path.strip()
        dest = data_dir / rel
        body = "\n".join(buf)
        if body and not body.endswith("\n"):
            # keep as joined
            pass
        if current_enc == "base64":
            raw = base64.b64decode("".join(line.strip() for line in buf), validate=False)
        else:
            raw = ("\n".join(buf) + ("\n" if buf else "")).encode("utf-8")
        if dry_run:
            print(f"  would write {rel} ({len(raw)} bytes)")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
            if rel == ".env" or rel.endswith(".key") or "key" in rel.lower():
                try:
                    dest.chmod(0o600)
                except OSError:
                    pass
            print(f"  + {rel}")
        written += 1
        current_path = None
        current_enc = "utf-8"
        buf = []

    for line in text.splitlines():
        m = BEGIN_RE.match(line)
        if m:
            flush()
            current_path = m.group("path").strip()
            current_enc = (m.group("enc") or "utf-8").strip()
            buf = []
            continue
        m2 = END_RE.match(line)
        if m2:
            flush()
            continue
        if current_path is not None:
            # skip pure comment lines only at file level — keep file content as-is
            buf.append(line)

    flush()
    if not dry_run:
        try:
            data_dir.chmod(0o700)
        except OSError:
            pass
    print(f"[jiyi] 已从 {jiyi} 恢复 {written} 个文件 → {data_dir}")
    return 0 if written else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="jiyi.txt 一键打包/恢复用户设置与 Key")
    ap.add_argument("command", choices=["save", "load", "path", "list"])
    ap.add_argument(
        "--data-dir",
        default=os.environ.get("AI_GATEWAY_HOME") or os.environ.get("DATA_DIR") or "",
        help="用户数据目录（默认 AI_GATEWAY_HOME 或当前目录）",
    )
    ap.add_argument(
        "--jiyi",
        default="",
        help="记忆文件路径（默认 AI_GATEWAY_JIYI 或 <code>/jiyi.txt）",
    )
    ap.add_argument("--code-dir", default="", help="仓库/安装代码根（用于默认 jiyi 路径）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    code_dir = Path(args.code_dir).resolve() if args.code_dir else Path(__file__).resolve().parents[1]
    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else code_dir
    jiyi = Path(args.jiyi).expanduser().resolve() if args.jiyi else default_jiyi_path(code_dir)

    if args.command == "path":
        print(jiyi)
        return 0
    if args.command == "list":
        if not jiyi.is_file():
            print(f"(empty) {jiyi}")
            return 0
        for line in jiyi.read_text(encoding="utf-8").splitlines():
            m = BEGIN_RE.match(line)
            if m:
                print(m.group("path"))
        return 0
    if args.command == "save":
        return pack(data_dir, jiyi)
    if args.command == "load":
        return unpack(jiyi, data_dir, dry_run=args.dry_run)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
