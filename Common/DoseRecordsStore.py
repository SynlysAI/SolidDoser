"""加样记录：SQLite 持久化。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

from Common.LocalDatabase import connect, init_schema

logger = logging.getLogger("soliddoser.dose_records")


@dataclass(frozen=True)
class DoseRecord:
    id: int
    dosed_at: str
    barcode: str
    target_mg: float
    actual_mg: Optional[float]
    station: Optional[int] = None


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def save_dose_record(
    *,
    barcode: str,
    target_mg: float,
    actual_mg: Optional[float],
    station: Optional[int] = None,
    dosed_at: Optional[str] = None,
) -> Tuple[bool, str]:
    """写入一条加样记录。成功时返回 (True, dosed_at)。"""
    try:
        init_schema()
        stamp = dosed_at or _now()
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO dose_records
                    (dosed_at, barcode, target_mg, actual_mg, station)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    stamp,
                    (barcode or "").strip(),
                    float(target_mg),
                    None if actual_mg is None else float(actual_mg),
                    None if station is None else int(station),
                ),
            )
            conn.commit()
        return True, stamp
    except Exception as exc:  # noqa: BLE001
        logger.warning("保存加样记录失败：%s", exc)
        return False, str(exc)


def load_dose_records(*, limit: int = 200) -> List[DoseRecord]:
    """按时间倒序加载最近记录。"""
    try:
        init_schema()
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT id, dosed_at, barcode, target_mg, actual_mg, station
                FROM dose_records
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [
            DoseRecord(
                id=int(row["id"]),
                dosed_at=str(row["dosed_at"] or ""),
                barcode=str(row["barcode"] or ""),
                target_mg=float(row["target_mg"] or 0.0),
                actual_mg=(
                    None
                    if row["actual_mg"] is None
                    else float(row["actual_mg"])
                ),
                station=(
                    None if row["station"] is None else int(row["station"])
                ),
            )
            for row in rows
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("加载加样记录失败：%s", exc)
        return []
