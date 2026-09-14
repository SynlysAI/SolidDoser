"""工位物料条码：SQLite 持久化（初始化成功写入，启动加载）。

兼容：若库中尚无记录，尝试从旧版 JSON 缓存迁移一次。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Tuple

from Common.AppLogging import PROJECT_ROOT
from Common.LocalDatabase import connect, get_db_path, init_schema
from Drivers.SolidDoserMotion import motion_config as cfg

if TYPE_CHECKING:
    from UIInteraction.ParameterManagement.SolidDoserSystemModel import (
        SolidDoserSystemState,
    )

logger = logging.getLogger("soliddoser.materials")

_LEGACY_JSON_NAME = "station_materials.json"


def materials_store_path() -> Path:
    """库文件路径（对外诊断用）。"""
    return get_db_path()


def _legacy_json_path() -> Path:
    return PROJECT_ROOT / "数据" / _LEGACY_JSON_NAME


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def save_station_materials(state: SolidDoserSystemState) -> Tuple[bool, str]:
    """将当前工位→条码表写入 SQLite（UPSERT 全表）。"""
    try:
        init_schema()
        stamp = _now()
        with connect() as conn:
            for station in cfg.iter_indexing_stations():
                barcode = (state.station_materials.get(station) or "").strip()
                conn.execute(
                    """
                    INSERT INTO station_materials (station, barcode, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(station) DO UPDATE SET
                        barcode = excluded.barcode,
                        updated_at = excluded.updated_at
                    """,
                    (int(station), barcode, stamp),
                )
            conn.commit()
        return True, str(get_db_path())
    except Exception as exc:  # noqa: BLE001
        logger.warning("保存物料条码失败：%s", exc)
        return False, str(exc)


def _read_rows(conn) -> Dict[int, str]:
    rows = conn.execute(
        "SELECT station, barcode FROM station_materials ORDER BY station"
    ).fetchall()
    mapping: Dict[int, str] = {
        s: "" for s in cfg.iter_indexing_stations()
    }
    for row in rows:
        station = int(row["station"])
        if station in mapping:
            mapping[station] = (row["barcode"] or "").strip()
    return mapping


def _migrate_legacy_json(conn) -> bool:
    """JSON → SQLite；成功后将 JSON 改名为 .migrated。"""
    path = _legacy_json_path()
    if not path.is_file():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("旧 JSON 物料缓存无法迁移：%s", exc)
        return False
    if not isinstance(raw, dict):
        return False
    stations_raw = raw.get("stations")
    if not isinstance(stations_raw, dict):
        return False

    stamp = _now()
    for s in cfg.iter_indexing_stations():
        val = stations_raw.get(str(s), stations_raw.get(s, ""))
        barcode = ("" if val is None else str(val)).strip()
        conn.execute(
            """
            INSERT INTO station_materials (station, barcode, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(station) DO UPDATE SET
                barcode = excluded.barcode,
                updated_at = excluded.updated_at
            """,
            (int(s), barcode, stamp),
        )
    conn.commit()
    try:
        migrated = path.with_suffix(path.suffix + ".migrated")
        path.replace(migrated)
    except OSError:
        logger.info("物料 JSON 已导入 SQLite，但未能重命名：%s", path)
    logger.info("已从 JSON 迁移物料条码到 SQLite：%s", path)
    return True


def clear_station_materials(state: SolidDoserSystemState) -> Tuple[bool, str]:
    """清空工位物料条码（内存 + SQLite），并作废 materials_ok。"""
    try:
        state.clear_materials()
        state.station_materials = {i: "" for i in cfg.iter_indexing_stations()}
        ok, detail = save_station_materials(state)
        if not ok:
            return False, detail
        return True, "物料表已清空"
    except Exception as exc:  # noqa: BLE001
        logger.warning("清空物料条码失败：%s", exc)
        return False, str(exc)


def load_station_materials(state: SolidDoserSystemState) -> Tuple[bool, str]:
    """从 SQLite 加载工位条码；成功则 materials_ok=True。

    库中无行时尝试迁移旧 JSON；仍无则返回 (False, 原因)。
    """
    try:
        init_schema()
        with connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM station_materials"
            ).fetchone()["n"]
            if count == 0:
                if not _migrate_legacy_json(conn):
                    return False, "无本地物料缓存"
            mapping = _read_rows(conn)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取物料条码失败：%s", exc)
        return False, f"读取失败: {exc}"

    state.station_materials = mapping
    state.materials_ok = True
    filled = sum(1 for c in mapping.values() if c)
    return True, (
        f"{get_db_path().name}（{filled}/{cfg.INDEXING_STATION_COUNT} 工位有码）"
    )
