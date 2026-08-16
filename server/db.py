"""SQLite 数据访问层：连接管理（WAL）、schema 初始化、轻量查询助手。

设计决策（评审 R2）：单连接 + 应用层写锁串行化。业务写负载为月级台账，
50 并发下无压力；所有 SQL 参数化，杜绝注入。
"""
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from . import config

_conn: sqlite3.Connection | None = None
_write_lock = threading.RLock()
_db_path: Path | None = None


def configure(db_path: Path | None = None) -> None:
    """指定数据库文件位置（测试时传入临时路径）。需在首次 get_conn 前调用。"""
    global _db_path, _conn
    if _conn is not None:
        _conn.close()
        _conn = None
    _db_path = db_path or Path(os.getenv("JUJIE_DB_PATH", str(config.DB_PATH)))


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        path = _db_path or Path(os.getenv("JUJIE_DB_PATH", str(config.DB_PATH)))
        path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.execute("PRAGMA busy_timeout=5000")
    return _conn


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return get_conn().execute(sql, params).fetchall()


def query_one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    return get_conn().execute(sql, params).fetchone()


def execute(sql: str, params: tuple = ()) -> int:
    """写操作（串行化），返回 lastrowid。"""
    with _write_lock:
        conn = get_conn()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def execute_many(sql: str, rows: list[tuple]) -> None:
    with _write_lock:
        conn = get_conn()
        conn.executemany(sql, rows)
        conn.commit()


def transaction():
    """显式事务上下文（批量导入等多步写入用）。"""
    return _Tx()


class _Tx:
    def __enter__(self) -> sqlite3.Connection:
        _write_lock.acquire()
        return get_conn()

    def __exit__(self, exc_type, exc, tb) -> None:
        conn = get_conn()
        if exc_type is None:
            conn.commit()
        else:
            conn.rollback()
        _write_lock.release()


SCHEMA = """
CREATE TABLE IF NOT EXISTS sys_dept (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  parent_id INTEGER,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT, created_by TEXT, updated_at TEXT, updated_by TEXT
);
CREATE TABLE IF NOT EXISTS sys_user (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  mobile TEXT,
  dept_id INTEGER REFERENCES sys_dept(id),
  role_code TEXT NOT NULL CHECK(role_code IN ('admin','manager','reporter','viewer','auditor')),
  ding_unionid TEXT UNIQUE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','active','disabled')),
  password_hash TEXT,
  session_revoked_at TEXT,
  created_at TEXT, created_by TEXT, updated_at TEXT, updated_by TEXT
);
CREATE TABLE IF NOT EXISTS ding_config (
  id INTEGER PRIMARY KEY CHECK(id=1),
  corp_id TEXT, qr_app_id TEXT, qr_app_secret_enc TEXT,
  app_key TEXT, app_secret_enc TEXT, admin_contact TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS auth_state (
  state TEXT PRIMARY KEY,
  expires_at INTEGER NOT NULL,
  bind_user_id INTEGER
);
CREATE TABLE IF NOT EXISTS op_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER, user_name TEXT, action TEXT, object_type TEXT, object_id TEXT,
  detail TEXT, ip TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS login_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  unionid TEXT, user_name TEXT, ip TEXT, ua TEXT, action TEXT, result TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS energy_type (
  code TEXT PRIMARY KEY,
  name TEXT NOT NULL, unit TEXT NOT NULL,
  tce_factor REAL NOT NULL DEFAULT 0,
  in_carbon INTEGER NOT NULL DEFAULT 1,
  is_green INTEGER NOT NULL DEFAULT 0,
  map_source_code TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT, created_by TEXT, updated_at TEXT, updated_by TEXT
);
CREATE TABLE IF NOT EXISTS energy_unit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, parent_id INTEGER,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT, created_by TEXT, updated_at TEXT, updated_by TEXT
);
CREATE TABLE IF NOT EXISTS energy_record (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  year INTEGER NOT NULL, month INTEGER NOT NULL,
  energy_type_code TEXT NOT NULL REFERENCES energy_type(code),
  unit_id INTEGER NOT NULL REFERENCES energy_unit(id),
  quantity REAL NOT NULL DEFAULT 0, amount REAL,
  gross_qty REAL, lease_deduct REAL, pv_qty REAL,
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','submitted','approved','rejected')),
  review_by INTEGER, review_at TEXT, reject_reason TEXT,
  remark TEXT, attachment TEXT,
  created_at TEXT, created_by TEXT, updated_at TEXT, updated_by TEXT,
  UNIQUE(year, month, energy_type_code, unit_id)
);
CREATE TABLE IF NOT EXISTS emission_source (
  code TEXT PRIMARY KEY,
  name_zh TEXT NOT NULL, name_en TEXT,
  scope TEXT NOT NULL CHECK(scope IN ('范围一','范围二','范围三')),
  source_type TEXT, unit TEXT NOT NULL, dept_code TEXT,
  guide TEXT, factor_ref TEXT,
  map_energy_type TEXT, map_convert REAL NOT NULL DEFAULT 1.0,
  enabled INTEGER NOT NULL DEFAULT 1, sort_no INTEGER DEFAULT 0,
  created_at TEXT, created_by TEXT, updated_at TEXT, updated_by TEXT
);
CREATE TABLE IF NOT EXISTS emission_factor (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_code TEXT NOT NULL REFERENCES emission_source(code),
  factor REAL NOT NULL,
  year_from INTEGER NOT NULL, year_to INTEGER,
  ref_source TEXT, change_reason TEXT,
  created_at TEXT, created_by TEXT, updated_at TEXT, updated_by TEXT,
  UNIQUE(source_code, year_from)
);
CREATE TABLE IF NOT EXISTS carbon_inventory (
  year INTEGER PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','submitted','approved')),
  submitted_by INTEGER, submitted_at TEXT, approved_by INTEGER, approved_at TEXT,
  created_at TEXT, created_by TEXT, updated_at TEXT, updated_by TEXT
);
CREATE TABLE IF NOT EXISTS carbon_activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  year INTEGER NOT NULL, source_code TEXT NOT NULL REFERENCES emission_source(code),
  activity_value REAL NOT NULL DEFAULT 0,
  data_origin TEXT NOT NULL DEFAULT 'manual' CHECK(data_origin IN ('manual','import','mapped')),
  factor_snapshot REAL, emission REAL,
  remark TEXT, reported_by INTEGER, reported_at TEXT,
  created_at TEXT, created_by TEXT, updated_at TEXT, updated_by TEXT,
  UNIQUE(year, source_code)
);
CREATE TABLE IF NOT EXISTS finance_metric (
  year INTEGER PRIMARY KEY, total_revenue REAL NOT NULL DEFAULT 0,
  created_at TEXT, created_by TEXT, updated_at TEXT, updated_by TEXT
);
CREATE TABLE IF NOT EXISTS carbon_target (
  id INTEGER PRIMARY KEY CHECK(id=1),
  target_year INTEGER, base_year INTEGER, base_emission REAL, total_reduction REAL,
  created_at TEXT, created_by TEXT, updated_at TEXT, updated_by TEXT
);
CREATE TABLE IF NOT EXISTS annual_plan (
  year INTEGER PRIMARY KEY,
  carbon_goal REAL, energy_goal_tce REAL, cost_budget REAL,
  created_at TEXT, created_by TEXT, updated_at TEXT, updated_by TEXT
);
CREATE TABLE IF NOT EXISTS customer (
  code TEXT PRIMARY KEY, name_zh TEXT NOT NULL, name_en TEXT,
  contact TEXT, enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT, created_by TEXT, updated_at TEXT, updated_by TEXT
);
CREATE TABLE IF NOT EXISTS customer_revenue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  year INTEGER NOT NULL, customer_code TEXT NOT NULL REFERENCES customer(code),
  revenue REAL NOT NULL DEFAULT 0,
  created_at TEXT, created_by TEXT, updated_at TEXT, updated_by TEXT,
  UNIQUE(year, customer_code)
);
CREATE TABLE IF NOT EXISTS import_task (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  biz_type TEXT NOT NULL, file_name TEXT, strategy TEXT,
  total INTEGER DEFAULT 0, success INTEGER DEFAULT 0, failed INTEGER DEFAULT 0,
  status TEXT DEFAULT 'parsed', error_file TEXT,
  created_at TEXT, created_by TEXT, updated_at TEXT, updated_by TEXT
);
CREATE TABLE IF NOT EXISTS export_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_type TEXT, year INTEGER, file_path TEXT, file_size INTEGER,
  created_at TEXT, created_by TEXT, updated_at TEXT, updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_energy_record_ym ON energy_record(year, month);
CREATE INDEX IF NOT EXISTS idx_carbon_activity_year ON carbon_activity(year);
CREATE INDEX IF NOT EXISTS idx_op_log_time ON op_log(created_at);
"""


def init_db() -> None:
    with _write_lock:
        conn = get_conn()
        conn.executescript(SCHEMA)
        # 旧库迁移：auth_state 增加 bind_user_id（钉钉扫码绑定）
        cols = [r[1] for r in conn.execute("PRAGMA table_info(auth_state)")]
        if "bind_user_id" not in cols:
            conn.execute("ALTER TABLE auth_state ADD COLUMN bind_user_id INTEGER")
        conn.commit()
