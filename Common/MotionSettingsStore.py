"""运动相关可配置参数（SQLite app_meta 持久化）。"""
from __future__ import annotations

import logging
from typing import Tuple

from Common.LocalDatabase import connect, init_schema
from Drivers.SolidDoserMotion import motion_config as cfg

logger = logging.getLogger("soliddoser.motion_settings")

_META_LIFT_WORK_MM = "lift_work_mm"

Result = Tuple[bool, str]


def load_motion_settings() -> Result:
    """启动时从数据库加载到 motion_config 运行时。"""
    try:
        init_schema()
        with connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_meta WHERE key = ?",
                (_META_LIFT_WORK_MM,),
            ).fetchone()
        if row is None or row["value"] is None or str(row["value"]).strip() == "":
            value = cfg.LIFT_WORK_MM_DEFAULT
        else:
            value = float(row["value"])
        applied = cfg.set_lift_work_mm(value)
        return True, f"升降工作位={applied:g} mm"
    except Exception as exc:  # noqa: BLE001
        cfg.set_lift_work_mm(cfg.LIFT_WORK_MM_DEFAULT)
        logger.warning("加载运动参数失败，使用默认升降工作位：%s", exc)
        return False, str(exc)


def save_lift_work_mm(value: float) -> Result:
    """校验、写入数据库，并更新运行时。"""
    try:
        applied = cfg.set_lift_work_mm(value)
        init_schema()
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO app_meta (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (_META_LIFT_WORK_MM, f"{applied:g}"),
            )
            conn.commit()
        return True, f"已保存升降工作位 {applied:g} mm（范围 {cfg.LIFT_WORK_MM_MIN:g}～{cfg.LIFT_WORK_MM_MAX:g}）"
    except Exception as exc:  # noqa: BLE001
        logger.warning("保存升降工作位失败：%s", exc)
        return False, str(exc)
