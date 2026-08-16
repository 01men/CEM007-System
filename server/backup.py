"""数据备份与恢复（FR-10.4）：每日自动备份（checkpoint 后复制，留 30 份）、
手动备份、上传恢复（校验文件头 → 二次确认 → 恢复前自动备份当前库）。
"""
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from . import config, db

SQLITE_MAGIC = b"SQLite format 3\x00"


def do_backup(manual: bool = False) -> Path:
    """WAL checkpoint 合并后复制数据库文件。"""
    conn = db.get_conn()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = "manual" if manual else "auto"
    dest = config.BACKUP_DIR / f"jujie_energy_{tag}_{ts}.db"
    shutil.copy2(db._db_path or config.DB_PATH, dest)
    _rotate()
    return dest


def _rotate() -> None:
    """保留最近 N 份备份。"""
    files = sorted(config.BACKUP_DIR.glob("jujie_energy_*.db"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[config.BACKUP_KEEP:]:
        old.unlink()


def list_backups() -> list[dict]:
    return [{"name": p.name, "size": p.stat().st_size,
             "created": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")}
            for p in sorted(config.BACKUP_DIR.glob("jujie_energy_*.db"),
                            key=lambda p: p.stat().st_mtime, reverse=True)]


def validate_backup(file_bytes: bytes) -> None:
    """校验备份文件头与可读性。"""
    if len(file_bytes) < 100 or not file_bytes.startswith(SQLITE_MAGIC):
        raise ValueError("不是合法的 SQLite 数据库文件")
    tmp = config.FILES_DIR / "_restore_check.db"
    tmp.write_bytes(file_bytes)
    try:
        conn = sqlite3.connect(str(tmp))
        conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        if "sys_user" not in tables or "energy_record" not in tables:
            raise ValueError("备份文件缺少核心数据表，可能不是本系统的备份")
    finally:
        tmp.unlink(missing_ok=True)


def restore(file_bytes: bytes) -> None:
    """恢复：先自动备份当前库，再整体替换（恢复后需重启生效）。"""
    validate_backup(file_bytes)
    do_backup(manual=True)  # 恢复前保护性备份
    target = db._db_path or config.DB_PATH
    db.get_conn().close()
    db._conn = None
    # 清理 WAL 副文件，防止旧日志污染新库
    for suffix in ("-wal", "-shm"):
        Path(str(target) + suffix).unlink(missing_ok=True)
    target.write_bytes(file_bytes)
    db.get_conn()  # 重新建立连接
