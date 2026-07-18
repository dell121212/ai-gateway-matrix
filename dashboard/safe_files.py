"""管理面配置文件的加锁写入工具。"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional


@contextmanager
def locked_file(path: Path) -> Iterator[None]:
    """跨请求串行化对同一文件的修改。"""
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
    lock_path = Path(tempfile.gettempdir()) / f"gwmatrix-{digest}.lock"
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _temp_dirs_for(path: Path) -> list[Path]:
    """同目录优先；Docker 单文件 mount 时 /app 往往只读，需回退可写目录。

    已知问题：dashboard 容器 uid=1000，/app 属 root 不可写，mkstemp(dir=/app)
    会 Permission denied，前端误显示「网络错误，保存失败」。
    """
    dirs: list[Path] = []
    parent = path.parent
    dirs.append(parent)
    # 项目 state 目录（compose 已挂成可写）
    for candidate in (parent / "state", Path("/app/state"), Path("/tmp")):
        if candidate not in dirs:
            dirs.append(candidate)
    tmp = Path(tempfile.gettempdir())
    if tmp not in dirs:
        dirs.append(tmp)
    return dirs


def _mkstemp_writable(path: Path) -> tuple[int, str]:
    last_err: Optional[OSError] = None
    for directory in _temp_dirs_for(path):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            return tempfile.mkstemp(prefix=f".{path.name}.", dir=str(directory))
        except OSError as exc:
            last_err = exc
            continue
    if last_err:
        raise last_err
    raise OSError(errno.EACCES, f"无法在任何目录创建临时文件: {path}")


def safe_rewrite(
    path: Path,
    content: str,
    *,
    mode: int,
    validator: Optional[Callable[[Path], None]] = None,
) -> None:
    """先写临时文件并校验，再原子替换目标。

    Docker 的「单文件 bind mount」常见两点限制：
    1. 挂载文件的父目录（如 /app）在容器内可能不可写 → 不能同目录 mkstemp
    2. rename 覆盖挂载点可能 EBUSY/EXDEV → 退化为 truncate 写

    因此临时文件优先写 state/ 或 /tmp，再 replace；跨设备则直接写目标。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = _mkstemp_writable(path)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp:
            temp.write(content)
            temp.flush()
            os.fsync(temp.fileno())
        os.chmod(temp_path, mode)
        if validator is not None:
            validator(temp_path)
        replaced = False
        try:
            os.replace(temp_path, path)
            replaced = True  # 临时文件已不存在
        except OSError as exc:
            if exc.errno not in (
                errno.EBUSY,
                errno.EXDEV,
                errno.EPERM,
                errno.EACCES,
            ):
                raise
            # 跨设备或挂载点：把已校验内容直接写入目标文件
            with path.open("w", encoding="utf-8") as target:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            try:
                os.chmod(path, mode)
            except OSError:
                pass
    finally:
        if not replaced:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
