"""流程导入命令 → 自动页同等动作。

签名兼容 ActionSequence 执行链：除 WAIT 外，首参为 DeviceManager，
从同一 ParameterStorage 构造 SolidDoserSystemContext（与自动页共享状态）。
失败抛 RuntimeError，供执行线程中断。
"""
from __future__ import annotations

from typing import Any, Tuple

from BusinessActions.DeviceManager import DeviceManager
from BusinessActions.SolidDoserSystem.system_actions import (
    system_run_bottle_discharge,
    system_run_device_init,
    system_run_dose,
    system_run_go_datum,
    system_run_go_origin,
    system_run_initialize,
    system_run_servo_on_all,
)
from BusinessActions.SolidDoserSystem.system_context import SolidDoserSystemContext
from Common.ActionLogger import get_action_logger
from UIInteraction.ParameterManagement.SolidDoserSystemModel import DoseItem

ActionResult = Tuple[bool, str]


def _system_ctx(dm: DeviceManager) -> SolidDoserSystemContext:
    return SolidDoserSystemContext(dm.parameter_storage)


def _require_ok(result: ActionResult, action: str) -> None:
    ok, msg = result
    text = (msg or "").strip() or action
    if not ok:
        raise RuntimeError(text)
    get_action_logger().record(f"工艺 · {action} · {text}")


def DEVICE_INIT(device_manager: DeviceManager) -> None:
    _require_ok(system_run_device_init(_system_ctx(device_manager)), "设备初始化")


def SERVO_ON(device_manager: DeviceManager) -> None:
    _require_ok(system_run_servo_on_all(_system_ctx(device_manager)), "使能")


def GO_DATUM(device_manager: DeviceManager) -> None:
    _require_ok(system_run_go_datum(_system_ctx(device_manager)), "回基准点")


def GO_ORIGIN(device_manager: DeviceManager) -> None:
    _require_ok(system_run_go_origin(_system_ctx(device_manager)), "回原点")


def MATERIAL_INIT(device_manager: DeviceManager) -> None:
    _require_ok(system_run_initialize(_system_ctx(device_manager)), "物料初始化")


def _parse_dose_items(args: tuple[Any, ...]) -> list[DoseItem]:
    if len(args) % 2 != 0:
        raise ValueError("加料参数须为 条码,目标(mg) 成对出现，例如 DOSE(MAT001, 10.5)")
    items: list[DoseItem] = []
    for i in range(0, len(args), 2):
        barcode = str(args[i]).strip()
        try:
            weight = float(args[i + 1])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"第{i // 2 + 1}条目标重量无效: {args[i + 1]}") from exc
        items.append(DoseItem(material_id=barcode, weight_mg=weight, actual_mg=None))
    return items


def DOSE(device_manager: DeviceManager, *args: Any) -> None:
    ctx = _system_ctx(device_manager)
    if args:
        ctx.system.dose_items = _parse_dose_items(args)
    for it in ctx.system.dose_items:
        it.actual_mg = None
    _require_ok(system_run_dose(ctx), "加料")


def BOTTLE_DISCHARGE(device_manager: DeviceManager) -> None:
    _require_ok(
        system_run_bottle_discharge(_system_ctx(device_manager)),
        "试剂瓶出料",
    )
