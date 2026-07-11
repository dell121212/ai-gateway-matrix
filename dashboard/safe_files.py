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


def safe_rewrite(
    path: Path,
    content: str,
    *,
    mode: int,
    validator: Optional[Callable[[Path], None]] = None,
) -> None:
    """先写同目录临时文件并校验，再原子替换目标。

    Docker 的“单文件 bind mount”在容器内不允许 rename 覆盖挂载点，
    遇到 EBUSY/EXDEV 时退化为“持锁 + truncate + fsync”；临时文件已经
    通过校验，因此即使无法原子 rename，也不会把未校验内容写入目标。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp:
            temp.write(content)
            temp.flush()
            os.fsync(temp.fileno())
        os.chmod(temp_path, mode)
        if validator is not None:
            validator(temp_path)
        try:
            os.replace(temp_path, path)
        except OSError as exc:
            if exc.errno not in (errno.EBUSY, errno.EXDEV, errno.EPERM):
                raise
            with path.open("w", encoding="utf-8") as target:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            os.chmod(path, mode)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
