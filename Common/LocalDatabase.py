"""SolidDoser 本机 SQLite 统一入口。

路径优先级：
1. SOLIDDOSER_DB
2. SOLIDDOSER_PROCESS_DB / RESIN_PROCESS_DB（兼容旧工艺库环境变量）
3. 打包：%LOCALAPPDATA%/SolidDoser/soliddoser.db
4. 开发：项目根/数据/soliddoser.db

后续本地持久化表集中在此 init_schema 中创建。
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

from Common.AppLogging import PROJECT_ROOT

_DB_FILE_NAME = "soliddoser.db"
_SCHEMA_READY = False


def get_db_path() -> Path:
    override = (
        os.environ.get("SOLIDDOSER_DB")
        or os.environ.get("SOLIDDOSER_PROCESS_DB")
        or os.environ.get("RESIN_PROCESS_DB")
    )
    if override:
        return Path(os.path.normpath(override))
    if getattr(sys, "frozen", False):
        base = Path(
            os.environ.get("LOCALAPPDATA", str(Path.home())),
            "SolidDoser",
        )
        return base / _DB_FILE_NAME
    return PROJECT_ROOT / "数据" / _DB_FILE_NAME


def connect() -> sqlite3.Connection:
    """打开连接；确保目录与表结构已就绪。"""
    init_schema()
    path = get_db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema() -> Path:
    """创建目录与全部本地表（可重复调用）。"""
    global _SCHEMA_READY
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS process_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                content TEXT NOT NULL,
                import_time TIMESTAMP NOT NULL,
                is_active INTEGER DEFAULT 0
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS station_materials (
                station INTEGER PRIMARY KEY,
                barcode TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS dose_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dosed_at TEXT NOT NULL,
                barcode TEXT NOT NULL,
                target_mg REAL NOT NULL,
                actual_mg REAL,
                station INTEGER
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_dose_records_dosed_at
            ON dose_records (dosed_at DESC)
            """
        )
        cur.execute(
            """
            INSERT OR IGNORE INTO app_meta (key, value)
            VALUES ('schema_version', '2')
            """
        )
        cur.execute(
            """
            UPDATE app_meta SET value = '2'
            WHERE key = 'schema_version' AND CAST(value AS INTEGER) < 2
            """
        )
        conn.commit()
    finally:
        conn.close()

    _SCHEMA_READY = True
    return path
