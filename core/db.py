"""共享 SQLite 连接策略：路径解析 + row_factory + WAL pragma + 事务上下文。

本仓库三处本地 SQLite（auth/users、usage/questions、teachers/chalaoshi）
原各自实现一份 _connect：完全相同的 mkdir + sqlite3.connect + Row factory
+ PRAGMA journal_mode=WAL + with db 事务 + close。任何 PRAGMA/事务策略调整
（foreign_keys=ON、超时、checkpoint 策略）以前要在三处复制，改完漏一处即静默
漂移；统一在这里，调用方写 `with db_connect(path) as db:` 即可。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def connect(db_path: str | None = None) -> Iterator[sqlite3.Connection]:
    """打开一个 SQLite 连接：父目录自动建、Row 工厂、WAL、事务上下文。

    用法：
        with db_connect("/path/to.db") as db:
            db.execute("SELECT 1")
        # 退出 with 时：成功则 commit、异常则 rollback；最后 close
    """
    path = Path(db_path) if db_path else None
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path) if path else ":memory:")
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA journal_mode=WAL")
        with db:  # 事务：正常提交，异常回滚
            yield db
    finally:
        db.close()