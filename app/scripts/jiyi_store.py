#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将用户设置与 Key 全部打包进 / 从单一「记忆」文件 jiyi.txt 恢复。

格式：AGM-JIYI-V1 多段文本（可人工查看；密钥文件请 chmod 600）。
默认路径：仓库根 jiyi.txt，可用环境变量 AI_GATEWAY_JIYI 覆盖。
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

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
    # data/redis 与 data/postgres 不直接打包；由 state 下的逻辑快照覆盖。
    # 原始数据库目录体积大、权限特殊，也不适合在运行中逐文件复制。
]

SKIP_NAME_PARTS = {
    "pg_stat_tmp",
    "postmaster.pid",
    "__pycache__",
    ".git",
}
MAX_FILE_BYTES = 8 * 1024 * 1024  # 单文件上限 8MiB，防止误塞巨型库
DATABASE_SNAPSHOT = Path("state/private-api-export.json.gz")
REDIS_SNAPSHOT = Path("state/gateway-redis-export.json.gz")
STATE_SNAPSHOT_MAX_BYTES = 256 * 1024 * 1024


def _warn(message: str, *, quiet: bool = False) -> None:
    if not quiet:
        print(f"[jiyi] {message}", file=sys.stderr)


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
        if part.startswith("client-keys.pre-migration-") and part.endswith(".bak"):
            return True
    return False


def _max_file_bytes(path: Path, data_dir: Path) -> int:
    try:
        if path.relative_to(data_dir) in {DATABASE_SNAPSHOT, REDIS_SNAPSHOT}:
            return STATE_SNAPSHOT_MAX_BYTES
    except ValueError:
        pass
    return MAX_FILE_BYTES


def collect_files(data_dir: Path, *, quiet: bool = False) -> list[Path]:
    files: list[Path] = []
    for entry in DEFAULT_ENTRIES:
        p = data_dir / entry
        try:
            exists = p.exists()
        except OSError as exc:
            _warn(f"skip unreadable path: {p} ({exc})", quiet=quiet)
            continue
        if not exists:
            continue
        try:
            is_file = p.is_file()
            is_dir = p.is_dir()
        except OSError as exc:
            _warn(f"skip unreadable path: {p} ({exc})", quiet=quiet)
            continue
        if is_file:
            try:
                if (
                    not _should_skip(p, data_dir)
                    and p.stat().st_size <= _max_file_bytes(p, data_dir)
                ):
                    files.append(p)
            except OSError as exc:
                _warn(f"skip unreadable file: {p} ({exc})", quiet=quiet)
            continue
        if is_dir:
            def onerror(exc: OSError) -> None:
                _warn(f"skip unreadable directory: {exc.filename or p} ({exc})", quiet=quiet)

            for root, dirnames, filenames in os.walk(p, topdown=True, onerror=onerror):
                root_path = Path(root)
                dirnames[:] = sorted(
                    name
                    for name in dirnames
                    if not _should_skip(root_path / name, data_dir)
                )
                for name in sorted(filenames):
                    f = root_path / name
                    if _should_skip(f, data_dir):
                        continue
                    try:
                        stat = f.stat()
                        is_file = f.is_file()
                    except OSError as exc:
                        _warn(f"skip unreadable file: {f} ({exc})", quiet=quiet)
                        continue
                    if not is_file:
                        continue
                    if stat.st_size > _max_file_bytes(f, data_dir):
                        _warn(f"skip large file: {f.relative_to(data_dir)}", quiet=quiet)
                        continue
                    if not os.access(f, os.R_OK):
                        _warn(f"skip unreadable file: {f.relative_to(data_dir)}", quiet=quiet)
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


def _locked_write_text(path: Path, content: str) -> None:
    """在同一 inode 上安全覆写，兼容 jiyi.txt 作为 Docker 单文件绑定挂载。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    stream: TextIO | None = None
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        stream = os.fdopen(fd, "r+", encoding="utf-8", newline="\n")
        fd = -1
        stream.seek(0)
        stream.write(content)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())
    finally:
        if stream is not None:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()
        elif fd >= 0:
            os.close(fd)


def _locked_read_text(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY)
    stream: TextIO | None = None
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        stream = os.fdopen(fd, "r", encoding="utf-8")
        fd = -1
        return stream.read()
    finally:
        if stream is not None:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()
        elif fd >= 0:
            os.close(fd)


def source_signature(data_dir: Path) -> tuple[tuple[str, int, int], ...]:
    """返回轻量签名；内容变化会触发延迟合并后的重新打包。"""

    data_dir = data_dir.resolve()
    signature: list[tuple[str, int, int]] = []
    for path in collect_files(data_dir, quiet=True):
        try:
            stat = path.stat()
        except OSError:
            continue
        signature.append(
            (path.relative_to(data_dir).as_posix(), stat.st_size, stat.st_mtime_ns)
        )
    return tuple(signature)


def pack(data_dir: Path, jiyi: Path, *, quiet: bool = False) -> int:
    data_dir = data_dir.resolve()
    if not data_dir.is_dir():
        print(f"[jiyi] DATA_DIR 不存在: {data_dir}", file=sys.stderr)
        return 2
    records: list[tuple[Path, bytes]] = []
    for path in collect_files(data_dir, quiet=quiet):
        try:
            records.append((path, path.read_bytes()))
        except OSError as exc:
            _warn(f"skip changed/unreadable file: {path} ({exc})", quiet=quiet)

    now = datetime.now(timezone.utc).isoformat()
    lines: list[str] = [
        f"# {HEADER}",
        f"# generated_at={now}",
        f"# data_dir={data_dir}",
        f"# file_count={len(records)}",
        "# 本文件含 API Key，请 chmod 600，勿提交 git",
        "",
    ]
    for f, raw in records:
        rel = f.relative_to(data_dir).as_posix()
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
    _locked_write_text(jiyi, content)
    if not quiet:
        print(f"[jiyi] 已写入 {jiyi} （{len(records)} 个文件, {len(content)} 字节）")
    for f, _raw in records:
        if not quiet:
            print(f"  + {f.relative_to(data_dir).as_posix()}")
    return 0


class AutoSync:
    """可测试的轮询同步状态机：发现变化后等待 debounce，再写一次快照。"""

    def __init__(self, data_dir: Path, jiyi: Path, *, debounce: float = 1.0) -> None:
        self.data_dir = data_dir.resolve()
        self.jiyi = jiyi.resolve()
        self.debounce = max(0.0, debounce)
        self.signature: tuple[tuple[str, int, int], ...] | None = None
        self.dirty_since: float | None = None

    def initialize(self) -> bool:
        if not self.data_dir.is_dir():
            raise FileNotFoundError(f"DATA_DIR 不存在: {self.data_dir}")
        self.signature = source_signature(self.data_dir)
        if not self.jiyi.is_file() or self.jiyi.stat().st_size == 0:
            if pack(self.data_dir, self.jiyi, quiet=True) != 0:
                raise RuntimeError("初始化 jiyi.txt 失败")
            return True
        try:
            self.jiyi.chmod(0o600)
        except OSError:
            pass
        return False

    def tick(self, *, now: float | None = None) -> bool:
        clock = time.monotonic() if now is None else now
        current = source_signature(self.data_dir)
        if current != self.signature:
            self.signature = current
            self.dirty_since = clock
            return False
        if self.dirty_since is None or clock - self.dirty_since < self.debounce:
            return False
        if pack(self.data_dir, self.jiyi, quiet=True) != 0:
            return False
        self.dirty_since = None
        return True


def watch(
    data_dir: Path,
    jiyi: Path,
    *,
    interval: float = 1.0,
    debounce: float = 1.0,
    ready_file: Path | None = None,
    database_url: str = "",
    database_snapshot: Path | None = None,
    database_interval: float = 5.0,
    redis_host: str = "",
    redis_port: int = 6379,
    redis_password: str = "",
    redis_snapshot: Path | None = None,
    redis_interval: float = 5.0,
) -> int:
    def export_database() -> bool:
        if not database_url or database_snapshot is None:
            return False
        import asyncio

        from jiyi_database import export_snapshot

        return asyncio.run(export_snapshot(database_url, database_snapshot))

    def export_redis() -> bool:
        if not redis_host or redis_snapshot is None:
            return False
        import asyncio

        from jiyi_redis import export_snapshot

        return asyncio.run(
            export_snapshot(
                redis_host,
                redis_port,
                redis_password,
                redis_snapshot,
            )
        )

    database_ready = not database_url or database_snapshot is None
    if database_url and database_snapshot is not None:
        try:
            export_database()
            database_ready = True
        except Exception as exc:
            print(f"[jiyi] 数据库首次快照失败，将重试: {exc}", file=sys.stderr, flush=True)

    redis_ready = not redis_host or redis_snapshot is None
    if redis_host and redis_snapshot is not None:
        try:
            export_redis()
            redis_ready = True
        except Exception as exc:
            print(f"[jiyi] Redis 首次快照失败，将重试: {exc}", file=sys.stderr, flush=True)

    sync = AutoSync(data_dir, jiyi, debounce=debounce)
    created = sync.initialize()
    action = "已自动生成" if created else "开始监听"
    print(f"[jiyi] {action}: {sync.jiyi}", flush=True)
    if ready_file is not None and database_ready and redis_ready:
        ready_file.parent.mkdir(parents=True, exist_ok=True)
        ready_file.touch()
    next_database_export = time.monotonic() + max(1.0, database_interval)
    next_redis_export = time.monotonic() + max(1.0, redis_interval)
    while True:
        time.sleep(max(0.2, interval))
        now = time.monotonic()
        if database_url and database_snapshot is not None and now >= next_database_export:
            next_database_export = now + max(1.0, database_interval)
            try:
                if export_database():
                    print("[jiyi] 已更新用户操作数据库快照", flush=True)
                database_ready = True
                if ready_file is not None and database_ready and redis_ready:
                    ready_file.parent.mkdir(parents=True, exist_ok=True)
                    ready_file.touch()
            except Exception as exc:
                print(
                    f"[jiyi] 数据库快照暂时失败，将重试: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
        if redis_host and redis_snapshot is not None and now >= next_redis_export:
            next_redis_export = now + max(1.0, redis_interval)
            try:
                if export_redis():
                    print("[jiyi] 已更新 Token 与路由状态快照", flush=True)
                redis_ready = True
                if ready_file is not None and database_ready and redis_ready:
                    ready_file.parent.mkdir(parents=True, exist_ok=True)
                    ready_file.touch()
            except Exception as exc:
                print(
                    f"[jiyi] Redis 快照暂时失败，将重试: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
        try:
            if sync.tick():
                print(f"[jiyi] 已自动同步: {sync.jiyi}", flush=True)
        except OSError as exc:
            print(f"[jiyi] 自动同步暂时失败，将重试: {exc}", file=sys.stderr, flush=True)


def unpack(jiyi: Path, data_dir: Path, *, dry_run: bool = False) -> int:
    jiyi = jiyi.resolve()
    data_dir = data_dir.resolve()
    if not jiyi.is_file():
        print(f"[jiyi] 文件不存在: {jiyi}", file=sys.stderr)
        return 2
    text = _locked_read_text(jiyi)
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
        dest = (data_dir / rel).resolve()
        try:
            dest.relative_to(data_dir)
        except ValueError:
            raise ValueError(f"jiyi 条目越过用户数据目录: {rel}")
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
    ap.add_argument("command", choices=["save", "load", "path", "list", "watch"])
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
    ap.add_argument("--interval", type=float, default=1.0, help="watch 扫描间隔秒数")
    ap.add_argument("--debounce", type=float, default=1.0, help="watch 变更合并秒数")
    ap.add_argument("--ready-file", default="", help="watch 就绪标志文件")
    ap.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="watch 导出的 PostgreSQL URL",
    )
    ap.add_argument("--database-snapshot", default="", help="数据库快照输出路径")
    ap.add_argument("--database-interval", type=float, default=5.0, help="数据库快照间隔秒数")
    ap.add_argument("--redis-host", default=os.environ.get("REDIS_HOST", ""))
    ap.add_argument("--redis-port", type=int, default=int(os.environ.get("REDIS_PORT", "6379")))
    ap.add_argument("--redis-password", default=os.environ.get("REDIS_PASSWORD", ""))
    ap.add_argument("--redis-snapshot", default="", help="Redis 快照输出路径")
    ap.add_argument("--redis-interval", type=float, default=5.0, help="Redis 快照间隔秒数")
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
        for line in _locked_read_text(jiyi).splitlines():
            m = BEGIN_RE.match(line)
            if m:
                print(m.group("path"))
        return 0
    if args.command == "save":
        return pack(data_dir, jiyi)
    if args.command == "load":
        return unpack(jiyi, data_dir, dry_run=args.dry_run)
    if args.command == "watch":
        ready_file = Path(args.ready_file) if args.ready_file else None
        return watch(
            data_dir,
            jiyi,
            interval=args.interval,
            debounce=args.debounce,
            ready_file=ready_file,
            database_url=args.database_url,
            database_snapshot=(
                Path(args.database_snapshot).resolve() if args.database_snapshot else None
            ),
            database_interval=args.database_interval,
            redis_host=args.redis_host,
            redis_port=args.redis_port,
            redis_password=args.redis_password,
            redis_snapshot=(
                Path(args.redis_snapshot).resolve() if args.redis_snapshot else None
            ),
            redis_interval=args.redis_interval,
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
