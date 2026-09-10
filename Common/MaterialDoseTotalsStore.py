"""物料累计加样量：SQLite 持久化（按条码汇总实际 mg）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

from Common.LocalDatabase import connect, init_schema

logger = logging.getLogger("soliddoser.material_dose_totals")


@dataclass(frozen=True)
class MaterialDoseTotal:
    barcode: str
    total_mg: float
    updated_at: str = ""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def add_actual_mg(barcode: str, actual_mg: float) -> Tuple[bool, str]:
    """按条码累加实际加样量（mg）。"""
    code = (barcode or "").strip()
    if not code:
        return False, "条码为空"
    try:
        delta = float(actual_mg)
    except (TypeError, ValueError):
        return False, "实际量无效"
    if delta == 0.0:
        return True, "跳过 0"
    try:
        init_schema()
        stamp = _now()
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO material_dose_totals (barcode, total_mg, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(barcode) DO UPDATE SET
                    total_mg = material_dose_totals.total_mg + excluded.total_mg,
                    updated_at = excluded.updated_at
                """,
                (code, delta, stamp),
            )
            conn.commit()
        return True, stamp
    except Exception as exc:  # noqa: BLE001
        logger.warning("累加物料加样量失败：%s", exc)
        return False, str(exc)


def clear_material_dose_totals() -> Tuple[bool, str]:
    """清空全部累计（物料初始化时调用）。"""
    try:
        init_schema()
        with connect() as conn:
            conn.execute("DELETE FROM material_dose_totals")
            conn.commit()
        return True, "已清零"
    except Exception as exc:  # noqa: BLE001
        logger.warning("清零物料累计加样量失败：%s", exc)
        return False, str(exc)


def reset_material_dose_totals(
    barcodes: Optional[Sequence[str]] = None,
) -> Tuple[bool, str]:
    """清零后按条码列表重建为 0（用于物料初始化完成）。"""
    try:
        init_schema()
        stamp = _now()
        codes: List[str] = []
        seen = set()
        for raw in barcodes or ():
            code = (raw or "").strip()
            if not code or code in seen:
                continue
            seen.add(code)
            codes.append(code)
        with connect() as conn:
            conn.execute("DELETE FROM material_dose_totals")
            for code in codes:
                conn.execute(
                    """
                    INSERT INTO material_dose_totals (barcode, total_mg, updated_at)
                    VALUES (?, 0, ?)
                    """,
                    (code, stamp),
                )
            conn.commit()
        return True, f"已重建 {len(codes)} 条"
    except Exception as exc:  # noqa: BLE001
        logger.warning("重建物料累计加样量失败：%s", exc)
        return False, str(exc)


def load_material_dose_totals() -> List[MaterialDoseTotal]:
    """按条码排序加载累计表。"""
    try:
        init_schema()
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT barcode, total_mg, updated_at
                FROM material_dose_totals
                ORDER BY barcode COLLATE NOCASE ASC
                """
            ).fetchall()
        return [
            MaterialDoseTotal(
                barcode=str(row["barcode"] or ""),
                total_mg=float(row["total_mg"] or 0.0),
                updated_at=str(row["updated_at"] or ""),
            )
            for row in rows
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("加载物料累计加样量失败：%s", exc)
        return []
