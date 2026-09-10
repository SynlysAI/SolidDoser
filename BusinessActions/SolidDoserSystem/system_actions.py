"""SolidDoser 系统状态机动作（框架；局部步骤后续可优化）。"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional, Sequence, Tuple

from BusinessActions.KeyenceScanner.scanner_actions import scanner_trigger_read
from BusinessActions.SolidDoserMotion import motion_actions as motion
from BusinessActions.SolidDoserSystem.system_context import SolidDoserSystemContext
from Common.ActionLogger import get_action_logger
from Common.DoseRecordsStore import save_dose_record
from Common.MaterialDoseTotalsStore import (
    add_actual_mg,
    clear_material_dose_totals,
    reset_material_dose_totals,
)
from Common.StationMaterialsStore import save_station_materials
from Drivers.SartoriusBalance.balance_driver import get_balance_driver
from Drivers.SolidDoserMotion import motion_config as cfg
from Drivers.SolidDoserMotion.indexing_disc import get_indexing_disc
from Drivers.SolidDoserMotion.motion_driver import (
    apply_status_to_state,
    get_motion_driver,
)
from UIInteraction.ParameterManagement.SolidDoserMotionModel import SolidDoserMotionState
from UIInteraction.ParameterManagement.SolidDoserSystemModel import (
    DoseItem,
    SystemRunState,
)

ActionResult = Tuple[bool, str]
_dose_log = logging.getLogger("soliddoser.dose")

# 加料闭环超时（秒）；净重判据见 Drivers.SartoriusBalance.balance_config
DOSE_MAX_WAIT_S = 180.0

# 回基准点判定轴（搅拌不参与；顺序与 system_run_go_datum 中轴回基准一致）
SYSTEM_DATUM_AXIS_KEYS: Sequence[str] = (
    cfg.LIFT_AXIS_KEY,
    cfg.HORIZONTAL_AXIS_KEY,
    cfg.POWDER_AXIS_KEY,
    cfg.INDEXING_AXIS_KEY,
)

_STIR_AXIS_KEY = "stirring"

# 「已回原点」判定轴：水平/升降/承粉在命名原点；分度停在任一工位；搅拌停止；
# 另要求振动与离子风扇停止
SYSTEM_ORIGIN_AXIS_KEYS: Sequence[str] = (
    cfg.HORIZONTAL_AXIS_KEY,
    cfg.INDEXING_AXIS_KEY,
    cfg.LIFT_AXIS_KEY,
    cfg.POWDER_AXIS_KEY,
    _STIR_AXIS_KEY,
)

# 「已回原点」判定用位置目标（分度除外：见 indexing_at_any_station）
_ORIGIN_TARGET_GETTERS: dict[str, Callable[[], float]] = {
    cfg.HORIZONTAL_AXIS_KEY: lambda: cfg.horizontal_position_mm("origin"),
    cfg.LIFT_AXIS_KEY: lambda: cfg.lift_position_mm("origin"),
    cfg.POWDER_AXIS_KEY: lambda: cfg.powder_position_mm("origin"),
}


def _ok(ctx: SolidDoserSystemContext, action: str, msg: str) -> ActionResult:
    ctx.system.last_action = action
    ctx.system.last_message = msg
    ctx.system.last_success = True
    return True, msg


def _fail(ctx: SolidDoserSystemContext, action: str, msg: str) -> ActionResult:
    ctx.system.last_action = action
    ctx.system.last_message = msg
    ctx.system.last_success = False
    return False, msg


def _sync_busy_flag(ctx: SolidDoserSystemContext) -> None:
    ctx.param_storage.is_system_busy = ctx.system.is_busy


def _sync_motion_status(ctx: SolidDoserSystemContext) -> None:
    status, err = get_motion_driver().read_status()
    apply_status_to_state(ctx.motion_ctx.motion, status)
    ctx.motion_ctx.motion.set_indexing_station(get_indexing_disc().current_station)
    if err:
        ctx.motion_ctx.motion.last_message = err


def _position_near(
    actual: float, target: float, *, wrap_span: float | None = None
) -> bool:
    tol = cfg.POSITION_MATCH_TOLERANCE
    if wrap_span is not None and wrap_span > 0:
        delta = abs(actual - target) % wrap_span
        delta = min(delta, wrap_span - delta)
        return delta <= tol
    return abs(actual - target) <= tol


def _axis_at_named_origin(motion_state: SolidDoserMotionState, axis_key: str) -> bool:
    if axis_key == _STIR_AXIS_KEY:
        # 搅拌轴：停止（非运动）即视为已回原点条件满足
        return not motion_state.axis(axis_key).moving
    actual = motion_state.axis(axis_key).actual_position
    if axis_key == cfg.INDEXING_AXIS_KEY:
        # 分度：停在任一工位（1～8）即视为原点条件满足，不限工位 1
        return cfg.indexing_at_any_station(actual)
    getter = _ORIGIN_TARGET_GETTERS.get(axis_key)
    if getter is None:
        return False
    return _position_near(actual, getter())


def refresh_system_flags(ctx: SolidDoserSystemContext) -> None:
    """根据当前轴状态刷新系统标识位（已使能 / 已回基准点 / 已回原点）。"""
    _sync_motion_status(ctx)
    motion_state = ctx.motion_ctx.motion
    ctx.system.enabled_ok = all(
        motion_state.axis(axis.key).servo_enabled for axis in cfg.AXES
    )
    ctx.system.datum_ok = all(
        motion_state.axis(key).datum_ok for key in SYSTEM_DATUM_AXIS_KEYS
    )
    ctx.system.origin_ok = all(
        _axis_at_named_origin(motion_state, key) for key in SYSTEM_ORIGIN_AXIS_KEYS
    ) and (not motion_state.do_states.get("vibration", False)) and (
        not motion_state.do_states.get("ion_fan", False)
    )


def _begin(
    ctx: SolidDoserSystemContext,
    new_state: SystemRunState,
    action: str,
    *,
    from_states: Sequence[SystemRunState] = (SystemRunState.IDLE,),
) -> Optional[ActionResult]:
    """仅允许从指定态进入忙碌态；失败时返回 ActionResult，成功返回 None。"""
    if new_state == SystemRunState.IDLE:
        return _fail(ctx, action, "内部错误：不能 begin 到空闲。")
    if ctx.system.run_state not in from_states:
        return _fail(
            ctx,
            action,
            f"系统忙碌（{ctx.system.state_label}），请等待完成或停止。",
        )
    ctx.system.abort_requested = False
    ctx.system.run_state = new_state
    ctx.system.last_action = action
    ctx.system.last_message = f"进入{new_state.value}"
    ctx.system.last_success = True
    ctx.system.clear_progress()
    _sync_busy_flag(ctx)
    return None


def _finish_to(
    ctx: SolidDoserSystemContext,
    ok: bool,
    action: str,
    msg: str,
    end_state: SystemRunState = SystemRunState.IDLE,
) -> ActionResult:
    """结束当前动作并进入指定态（默认空闲）。"""
    ctx.system.run_state = end_state
    ctx.system.abort_requested = False
    if end_state == SystemRunState.IDLE:
        ctx.system.bottle_feed_request_id = ""
        ctx.system.bottle_discharge_request_id = ""
        ctx.system.bottle_discharge_peer_done = False
        ctx.system.host_dose_request_id = ""
        ctx.system.clear_progress()
    _sync_busy_flag(ctx)
    refresh_system_flags(ctx)
    if ok:
        return _ok(ctx, action, msg)
    return _fail(ctx, action, msg)


def _finish(
    ctx: SolidDoserSystemContext,
    ok: bool,
    action: str,
    msg: str,
) -> ActionResult:
    return _finish_to(ctx, ok, action, msg, SystemRunState.IDLE)


def _aborted(ctx: SolidDoserSystemContext) -> bool:
    return ctx.system.abort_requested


def system_request_abort(ctx: SolidDoserSystemContext) -> ActionResult:
    """请求停止当前忙碌流程（由运行中的步骤轮询生效）。

    等待握手的占位态（进料/出料/等待进瓶/等待取瓶）停止时立即回到空闲。
    """
    action = "停止"
    if not ctx.system.is_busy:
        return _fail(ctx, action, "当前空闲，无需停止。")
    if ctx.system.run_state in (
        SystemRunState.FEEDING_BOTTLE,
        SystemRunState.DISCHARGING_BOTTLE,
        SystemRunState.WAITING_BOTTLE,
        SystemRunState.WAITING_PICKUP,
    ):
        return _finish(
            ctx,
            False,
            action,
            f"{ctx.system.state_label}已由本机停止解除，请通知主系统中止对应流程。",
        )
    ctx.system.abort_requested = True
    ctx.system.last_message = "已请求停止…"
    return _ok(ctx, action, "已请求停止，等待当前步骤结束。")


def system_bottle_feed_request(
    ctx: SolidDoserSystemContext, request_id: str = ""
) -> Tuple[bool, str, dict]:
    """主系统进料请求：空闲 + 在原点 + 试剂瓶3无瓶时进入「试剂瓶进料中」并锁定本机动作。

    返回 (accepted, message, response_dict)。
    """
    action = "试剂瓶进料请求"
    rid = (request_id or "").strip()
    base = {"cmd": "BOTTLE_FEED_REQUEST", "request_id": rid}

    refresh_system_flags(ctx)
    if ctx.system.run_state != SystemRunState.IDLE:
        reason = "not_idle"
        msg = f"系统忙碌（{ctx.system.state_label}），不具备进料条件。"
        return False, msg, {**base, "status": "rejected", "reason": reason, "message": msg}

    if not ctx.system.origin_ok:
        reason = "not_at_origin"
        msg = "系统未在原点位置，不具备进料条件。"
        return False, msg, {**base, "status": "rejected", "reason": reason, "message": msg}

    bottle3 = bool(ctx.motion_ctx.motion.di_states.get("bottle_3", False))
    if bottle3:
        reason = "bottle3_occupied"
        msg = "试剂瓶3已有瓶，无需进料。"
        return False, msg, {**base, "status": "rejected", "reason": reason, "message": msg}

    blocked = _begin(ctx, SystemRunState.FEEDING_BOTTLE, action)
    if blocked is not None:
        reason = "not_idle"
        msg = blocked[1]
        return False, msg, {**base, "status": "rejected", "reason": reason, "message": msg}

    ctx.system.bottle_feed_request_id = rid
    ctx.system.set_progress(0, 1, "等待主系统进料完成")
    ctx.system.last_message = "已接受进料请求，等待 BOTTLE_FEED_DONE。"
    msg = "可以进料。"
    return True, msg, {**base, "status": "accepted", "message": msg}


def system_bottle_feed_done(
    ctx: SolidDoserSystemContext, request_id: str = ""
) -> Tuple[bool, str, dict]:
    """主系统进料完成：校验状态与 request_id 后回到空闲。"""
    action = "试剂瓶进料完成"
    rid = (request_id or "").strip()
    base = {"cmd": "BOTTLE_FEED_DONE", "request_id": rid}

    if ctx.system.run_state != SystemRunState.FEEDING_BOTTLE:
        reason = "not_feeding"
        msg = f"当前非进料态（{ctx.system.state_label}），忽略完成信号。"
        return False, msg, {**base, "status": "error", "reason": reason, "message": msg}

    expected = (ctx.system.bottle_feed_request_id or "").strip()
    if expected and rid and expected != rid:
        reason = "request_id_mismatch"
        msg = f"request_id 不匹配（期望 {expected}，收到 {rid}）。"
        return False, msg, {**base, "status": "error", "reason": reason, "message": msg}

    _finish(ctx, True, action, "主系统进料完成，已回到空闲。")
    msg = "进料流程结束。"
    return True, msg, {**base, "status": "success", "message": msg}


def system_bottle_discharge_done(
    ctx: SolidDoserSystemContext, request_id: str = ""
) -> Tuple[bool, str, dict]:
    """主系统出料完成信号：校验后置位，由出料等待线程结束流程。"""
    rid = (request_id or "").strip()
    base = {"cmd": "BOTTLE_DISCHARGE_DONE", "request_id": rid}

    if ctx.system.run_state != SystemRunState.DISCHARGING_BOTTLE:
        reason = "not_discharging"
        msg = f"当前非出料态（{ctx.system.state_label}），忽略完成信号。"
        return False, msg, {**base, "status": "error", "reason": reason, "message": msg}

    expected = (ctx.system.bottle_discharge_request_id or "").strip()
    if expected and rid and expected != rid:
        reason = "request_id_mismatch"
        msg = f"request_id 不匹配（期望 {expected}，收到 {rid}）。"
        return False, msg, {**base, "status": "error", "reason": reason, "message": msg}

    ctx.system.bottle_discharge_peer_done = True
    ctx.system.last_message = "已收到主系统出料完成信号。"
    msg = "出料完成信号已接收。"
    return True, msg, {**base, "status": "success", "message": msg}


def dose_items_payload(ctx: SolidDoserSystemContext) -> list[dict]:
    out: list[dict] = []
    for it in ctx.system.dose_items or []:
        out.append(
            {
                "barcode": (it.material_id or "").strip(),
                "target_mg": float(it.weight_mg or 0.0),
                "actual_mg": it.actual_mg,
            }
        )
    return out


def system_host_begin_device_init(
    ctx: SolidDoserSystemContext, request_id: str = ""
) -> Tuple[bool, str, dict]:
    """主机：设备初始化请求（同步占用态并回 accepted，实际步骤由后台线程执行）。"""
    from Common.HostBottleFeedConfig import CMD_DEVICE_INIT_REQUEST

    action = "设备初始化"
    rid = (request_id or "").strip()
    base = {"cmd": CMD_DEVICE_INIT_REQUEST, "request_id": rid}
    blocked = _begin(ctx, SystemRunState.DEVICE_INITING, action)
    if blocked is not None:
        return (
            False,
            blocked[1],
            {
                **base,
                "status": "rejected",
                "reason": "not_idle",
                "message": blocked[1],
            },
        )
    msg = "已接受设备初始化请求。"
    ctx.system.last_message = msg
    return True, msg, {**base, "status": "accepted", "message": msg}


def system_host_begin_material_init(
    ctx: SolidDoserSystemContext, request_id: str = ""
) -> Tuple[bool, str, dict]:
    """主机：物料初始化请求（同步占用态）。"""
    from Common.HostBottleFeedConfig import CMD_MATERIAL_INIT_REQUEST

    action = "物料初始化"
    rid = (request_id or "").strip()
    base = {"cmd": CMD_MATERIAL_INIT_REQUEST, "request_id": rid}
    refresh_system_flags(ctx)
    if not ctx.system.enabled_ok:
        msg = "未使能，请先执行设备初始化/使能。"
        return False, msg, {
            **base,
            "status": "rejected",
            "reason": "not_enabled",
            "message": msg,
        }
    if not ctx.system.datum_ok:
        msg = "未回基准点，请先执行设备初始化/回基准点。"
        return False, msg, {
            **base,
            "status": "rejected",
            "reason": "not_datum",
            "message": msg,
        }
    if not ctx.system.origin_ok:
        msg = "未回原点，请先执行设备初始化/回原点。"
        return False, msg, {
            **base,
            "status": "rejected",
            "reason": "not_at_origin",
            "message": msg,
        }
    blocked = _begin(ctx, SystemRunState.INITIALIZING, action)
    if blocked is not None:
        return (
            False,
            blocked[1],
            {
                **base,
                "status": "rejected",
                "reason": "not_idle",
                "message": blocked[1],
            },
        )
    msg = "已接受物料初始化请求。"
    ctx.system.last_message = msg
    return True, msg, {**base, "status": "accepted", "message": msg}


def system_host_dose_request(
    ctx: SolidDoserSystemContext,
    items: list,
    request_id: str = "",
) -> Tuple[bool, str, dict]:
    """主机加料申请：校验空闲+原点+条码，写入 dose_items，不立刻加料。"""
    from Common.HostBottleFeedConfig import CMD_DOSE_REQUEST

    rid = (request_id or "").strip()
    base: dict = {"cmd": CMD_DOSE_REQUEST, "request_id": rid}

    if ctx.system.run_state != SystemRunState.IDLE:
        msg = f"系统忙碌（{ctx.system.state_label}），不响应加料申请。"
        return False, msg, {
            **base,
            "status": "rejected",
            "reason": "not_idle",
            "message": msg,
        }

    refresh_system_flags(ctx)
    if not ctx.system.origin_ok:
        msg = "固体加样模块不在原点，不响应加料申请。"
        return False, msg, {
            **base,
            "status": "rejected",
            "reason": "not_at_origin",
            "message": msg,
        }

    if not ctx.system.materials_ok:
        msg = "物料表未初始化，不响应加料申请。"
        return False, msg, {
            **base,
            "status": "rejected",
            "reason": "materials_not_ready",
            "message": msg,
        }

    if not isinstance(items, list) or not items:
        msg = "加料清单为空或格式无效。"
        return False, msg, {
            **base,
            "status": "rejected",
            "reason": "bad_items",
            "message": msg,
        }

    parsed: list[DoseItem] = []
    missing: list[str] = []
    for i, raw in enumerate(items, start=1):
        if not isinstance(raw, dict):
            msg = f"第{i}条物料不是对象。"
            return False, msg, {
                **base,
                "status": "rejected",
                "reason": "bad_items",
                "message": msg,
            }
        barcode = str(
            raw.get("barcode") or raw.get("material_id") or ""
        ).strip()
        try:
            target = float(
                raw.get("target_mg", raw.get("weight_mg", raw.get("weight", 0)))
            )
        except (TypeError, ValueError):
            target = 0.0
        if not barcode or target <= 0:
            msg = f"第{i}条条码或目标重量无效。"
            return False, msg, {
                **base,
                "status": "rejected",
                "reason": "bad_items",
                "message": msg,
            }
        if ctx.system.find_station_by_material(barcode) is None:
            missing.append(barcode)
        parsed.append(DoseItem(material_id=barcode, weight_mg=target, actual_mg=None))

    if missing:
        msg = "物料条码不在物料表中：" + ", ".join(missing)
        return False, msg, {
            **base,
            "status": "rejected",
            "reason": "barcode_missing",
            "message": msg,
            "missing": missing,
        }

    ctx.system.dose_items = parsed
    ctx.system.host_dose_request_id = rid
    ctx.system.clear_dose_live()
    msg = f"可以响应加料申请（{len(parsed)} 条）。"
    ctx.system.last_action = "加料申请"
    ctx.system.last_message = msg
    ctx.system.last_success = True
    return True, msg, {
        **base,
        "status": "accepted",
        "message": msg,
        "items": dose_items_payload(ctx),
    }


def system_host_dose_confirm(
    ctx: SolidDoserSystemContext, request_id: str = ""
) -> Tuple[bool, str, dict]:
    """设备已响应申请 → 进入等待进瓶。"""
    from Common.HostBottleFeedConfig import CMD_DOSE_CONFIRM

    rid = (request_id or "").strip()
    base = {"cmd": CMD_DOSE_CONFIRM, "request_id": rid}
    expected = (ctx.system.host_dose_request_id or "").strip()
    if expected and rid and expected != rid:
        msg = f"request_id 不匹配（期望 {expected}，收到 {rid}）。"
        return False, msg, {
            **base,
            "status": "error",
            "reason": "request_id_mismatch",
            "message": msg,
        }
    if ctx.system.run_state != SystemRunState.IDLE:
        msg = f"当前非空闲（{ctx.system.state_label}），无法确认加料。"
        return False, msg, {
            **base,
            "status": "error",
            "reason": "not_idle",
            "message": msg,
        }
    if not ctx.system.dose_items:
        msg = "无已接受的加料清单，请先 DOSE_REQUEST。"
        return False, msg, {
            **base,
            "status": "error",
            "reason": "no_dose_items",
            "message": msg,
        }
    if rid:
        ctx.system.host_dose_request_id = rid
    blocked = _begin(ctx, SystemRunState.WAITING_BOTTLE, "等待进瓶")
    if blocked is not None:
        return False, blocked[1], {
            **base,
            "status": "error",
            "reason": "not_idle",
            "message": blocked[1],
        }
    ctx.system.set_progress(0, 1, "等待设备放瓶")
    msg = "已进入等待进瓶。"
    return True, msg, {
        **base,
        "status": "ok",
        "message": msg,
        "state": "waiting_bottle",
    }


def system_host_begin_bottle_placed(
    ctx: SolidDoserSystemContext, request_id: str = ""
) -> Tuple[bool, str, dict]:
    """已放瓶：从等待进瓶转入加料中（后台执行 dose）。"""
    from Common.HostBottleFeedConfig import CMD_BOTTLE_PLACED

    rid = (request_id or "").strip()
    base = {"cmd": CMD_BOTTLE_PLACED, "request_id": rid}
    expected = (ctx.system.host_dose_request_id or "").strip()
    if expected and rid and expected != rid:
        msg = f"request_id 不匹配（期望 {expected}，收到 {rid}）。"
        return False, msg, {
            **base,
            "status": "error",
            "reason": "request_id_mismatch",
            "message": msg,
        }
    if ctx.system.run_state != SystemRunState.WAITING_BOTTLE:
        msg = f"当前非等待进瓶（{ctx.system.state_label}）。"
        return False, msg, {
            **base,
            "status": "error",
            "reason": "not_waiting_bottle",
            "message": msg,
        }
    blocked = _begin(
        ctx,
        SystemRunState.DOSING,
        "加料",
        from_states=(SystemRunState.WAITING_BOTTLE,),
    )
    if blocked is not None:
        return False, blocked[1], {
            **base,
            "status": "error",
            "reason": "busy",
            "message": blocked[1],
        }
    msg = "已放瓶，开始加样。"
    return True, msg, {
        **base,
        "status": "ok",
        "message": msg,
        "state": "dosing",
    }


def system_host_bottle_removed(
    ctx: SolidDoserSystemContext, request_id: str = ""
) -> Tuple[bool, str, dict]:
    """取瓶完成 → 空闲。"""
    from Common.HostBottleFeedConfig import CMD_BOTTLE_REMOVED

    rid = (request_id or "").strip()
    base = {"cmd": CMD_BOTTLE_REMOVED, "request_id": rid}
    expected = (ctx.system.host_dose_request_id or "").strip()
    if expected and rid and expected != rid:
        msg = f"request_id 不匹配（期望 {expected}，收到 {rid}）。"
        return False, msg, {
            **base,
            "status": "error",
            "reason": "request_id_mismatch",
            "message": msg,
        }
    if ctx.system.run_state != SystemRunState.WAITING_PICKUP:
        msg = f"当前非等待取瓶（{ctx.system.state_label}）。"
        return False, msg, {
            **base,
            "status": "error",
            "reason": "not_waiting_pickup",
            "message": msg,
        }
    _finish(ctx, True, "取瓶完成", "设备已取瓶，固体加样回到空闲。")
    msg = "已回到空闲。"
    return True, msg, {
        **base,
        "status": "success",
        "message": msg,
        "state": "idle",
    }


def system_run_bottle_discharge(ctx: SolidDoserSystemContext) -> ActionResult:
    """空闲 → 试剂瓶出料中 → 空闲。

    条件：空闲 + 在原点 + 试剂瓶3有瓶。
    向主系统发送 BOTTLE_DISCHARGE_REQUEST，等待 BOTTLE_DISCHARGE_DONE（默认 5 分钟超时）。
    """
    from Common.HostBottleFeedConfig import BOTTLE_DISCHARGE_TIMEOUT_S
    from BusinessActions.HostComm.bottle_feed_udp import get_bottle_comm

    action = "试剂瓶出料"
    refresh_system_flags(ctx)
    if ctx.system.run_state != SystemRunState.IDLE:
        return _fail(ctx, action, f"系统忙碌（{ctx.system.state_label}），无法出料。")
    if not ctx.system.origin_ok:
        return _fail(ctx, action, "系统未在原点位置，无法出料。")
    bottle3 = bool(ctx.motion_ctx.motion.di_states.get("bottle_3", False))
    if not bottle3:
        return _fail(ctx, action, "试剂瓶3无瓶，无法出料。")

    blocked = _begin(ctx, SystemRunState.DISCHARGING_BOTTLE, action)
    if blocked is not None:
        return blocked

    import uuid

    rid = str(uuid.uuid4())
    ctx.system.bottle_discharge_request_id = rid
    ctx.system.bottle_discharge_peer_done = False
    ctx.system.set_progress(0, 2, "向主系统发送出料请求")

    try:
        try:
            comm = get_bottle_comm()
        except RuntimeError as exc:
            return _finish(ctx, False, action, str(exc))

        sent = comm.send_to_host(
            {
                "cmd": "BOTTLE_DISCHARGE_REQUEST",
                "request_id": rid,
                "message": "加样系统请求试剂瓶出料",
            }
        )
        if not sent:
            return _finish(ctx, False, action, "向主系统发送出料请求失败。")

        ctx.system.set_progress(1, 2, "等待主系统出料完成")
        ctx.system.last_message = (
            f"已发送出料请求（{rid[:8]}…），等待 BOTTLE_DISCHARGE_DONE…"
        )

        deadline = time.monotonic() + float(BOTTLE_DISCHARGE_TIMEOUT_S)
        while time.monotonic() < deadline:
            if _aborted(ctx):
                return _finish(ctx, False, action, "出料已停止。")
            # 停止键对出料态会直接 _finish；若已空闲则退出等待
            if ctx.system.run_state != SystemRunState.DISCHARGING_BOTTLE:
                return (
                    ctx.system.last_success,
                    ctx.system.last_message or "出料已结束。",
                )
            if ctx.system.bottle_discharge_peer_done:
                return _finish(ctx, True, action, "试剂瓶出料完成。")
            time.sleep(0.2)

        return _finish(
            ctx,
            False,
            action,
            f"等待主系统出料完成超时（{BOTTLE_DISCHARGE_TIMEOUT_S:g} s）。",
        )
    except Exception as exc:  # noqa: BLE001
        return _finish(ctx, False, action, f"出料异常: {exc}")


def system_refresh_status(ctx: SolidDoserSystemContext) -> ActionResult:
    action = "刷新系统状态"
    refresh_system_flags(ctx)
    return _ok(ctx, action, ctx.system.overview_summary())


def _run_servo_on_all_steps(ctx: SolidDoserSystemContext) -> ActionResult:
    """按显示顺序使能全部电机（不切换系统运行态）。"""
    keys = [axis.key for axis in cfg.AXES]
    ctx.system.set_progress(0, len(keys), "准备使能")
    try:
        for i, axis_key in enumerate(keys):
            if _aborted(ctx):
                return False, "使能已中止。"
            label = cfg.AXIS_BY_KEY[axis_key].label
            ctx.system.set_progress(i, len(keys), f"{label} 使能")
            ok, detail = motion.motion_axis_servo_on(ctx.motion_ctx, axis_key)
            if not ok:
                return False, detail
            ctx.system.set_progress(i + 1, len(keys), f"{label} 完成")
        return True, "全部电机已使能。"
    except Exception as exc:  # noqa: BLE001
        return False, f"使能异常: {exc}"


def _run_go_datum_steps(ctx: SolidDoserSystemContext) -> ActionResult:
    """执行回基准点各步骤（不切换系统运行态）。"""
    steps: list[tuple[str, Callable[[], ActionResult]]] = [
        ("振动电机停止", lambda: motion.motion_do_set(ctx.motion_ctx, "vibration", False)),
        ("风扇停止", lambda: motion.motion_do_set(ctx.motion_ctx, "ion_fan", False)),
        ("搅拌电机停止", lambda: motion.motion_axis_stop(ctx.motion_ctx, _STIR_AXIS_KEY)),
        (
            "升降电机回基准点",
            lambda: motion.motion_axis_go_datum(ctx.motion_ctx, cfg.LIFT_AXIS_KEY),
        ),
        (
            "水平电机回基准点",
            lambda: motion.motion_axis_go_datum(ctx.motion_ctx, cfg.HORIZONTAL_AXIS_KEY),
        ),
        (
            "承粉电机回基准点",
            lambda: motion.motion_axis_go_datum(ctx.motion_ctx, cfg.POWDER_AXIS_KEY),
        ),
        (
            "分度电机回基准点",
            lambda: motion.motion_axis_go_datum(ctx.motion_ctx, cfg.INDEXING_AXIS_KEY),
        ),
    ]
    total = len(steps)
    ctx.system.set_progress(0, total, "准备回基准点")
    try:
        for i, (label, runner) in enumerate(steps):
            if _aborted(ctx):
                return False, "回基准点已中止。"
            ctx.system.set_progress(i, total, label)
            ok, detail = runner()
            if not ok:
                return False, detail
            ctx.system.set_progress(i + 1, total, f"{label} 完成")
        return True, "系统回基准点完成。"
    except Exception as exc:  # noqa: BLE001
        return False, f"回基准点异常: {exc}"


def _run_go_origin_steps(ctx: SolidDoserSystemContext) -> ActionResult:
    """执行回原点各步骤（不切换系统运行态）。"""
    steps: list[tuple[str, Callable[[], ActionResult]]] = [
        ("振动电机停止", lambda: motion.motion_do_set(ctx.motion_ctx, "vibration", False)),
        ("离子风扇停止", lambda: motion.motion_do_set(ctx.motion_ctx, "ion_fan", False)),
        ("搅拌电机停止", lambda: motion.motion_axis_stop(ctx.motion_ctx, _STIR_AXIS_KEY)),
        (
            "升降电机回原点",
            lambda: motion.motion_lift_go_origin(ctx.motion_ctx),
        ),
        (
            "水平电机回原点",
            lambda: motion.motion_horizontal_go_origin(ctx.motion_ctx),
        ),
        (
            "承粉电机回原点",
            lambda: motion.motion_powder_go_origin(ctx.motion_ctx),
        ),
        (
            "分度电机回原点",
            lambda: motion.motion_indexing_go_origin(ctx.motion_ctx),
        ),
    ]
    total = len(steps)
    ctx.system.set_progress(0, total, "准备回原点")
    try:
        for i, (label, runner) in enumerate(steps):
            if _aborted(ctx):
                return False, "回原点已中止。"
            ctx.system.set_progress(i, total, label)
            ok, detail = runner()
            if not ok:
                return False, detail
            ctx.system.set_progress(i + 1, total, f"{label} 完成")
        return True, "系统回原点完成。"
    except Exception as exc:  # noqa: BLE001
        return False, f"回原点异常: {exc}"


def system_run_servo_on_all(ctx: SolidDoserSystemContext) -> ActionResult:
    """空闲 → 使能中 → 空闲。按显示顺序使能全部电机。"""
    action = "系统使能"
    blocked = _begin(ctx, SystemRunState.ENABLING, action)
    if blocked is not None:
        return blocked
    ok, detail = _run_servo_on_all_steps(ctx)
    return _finish(ctx, ok, action, detail)


def system_run_go_datum(ctx: SolidDoserSystemContext) -> ActionResult:
    """空闲 → 回基准点中 → 空闲。

    顺序（逐步完成，避免机构干涉）：
    振动关 → 风扇关 → 搅拌停 → 升降回基准 → 水平回基准 → 承粉回基准
    → 分度回基准。
    """
    action = "系统回基准点"
    blocked = _begin(ctx, SystemRunState.HOMING, action)
    if blocked is not None:
        return blocked
    ok, detail = _run_go_datum_steps(ctx)
    return _finish(ctx, ok, action, detail)


def system_run_go_origin(ctx: SolidDoserSystemContext) -> ActionResult:
    """空闲 → 回原点中 → 空闲。

    顺序：振动停 → 风扇停 → 搅拌停 → 升降回原点 → 水平回原点
    → 承粉回原点 → 分度回原点（命名原点 / 工位 1）。
    使能/基准不在入口统一校验，由各轴回原点动作逐步检查。
    """
    action = "系统回原点"
    blocked = _begin(ctx, SystemRunState.GOING_ORIGIN, action)
    if blocked is not None:
        return blocked
    ok, detail = _run_go_origin_steps(ctx)
    return _finish(ctx, ok, action, detail)


def system_run_device_init(
    ctx: SolidDoserSystemContext,
    *,
    already_begun: bool = False,
) -> ActionResult:
    """空闲 → 设备初始化中 → 空闲。

    全程保持忙碌，依次：使能 → 回基准点 → 回原点，避免阶段间隙被其它流程插入。
    任一步失败或中止则停止后续步骤。
    already_begun=True 时表示调用方已进入 DEVICE_INITING（主机 UDP 先回 accepted）。
    """
    action = "设备初始化"
    if not already_begun:
        blocked = _begin(ctx, SystemRunState.DEVICE_INITING, action)
        if blocked is not None:
            return blocked

    try:
        ok, detail = _run_servo_on_all_steps(ctx)
        if not ok:
            return _finish(ctx, False, action, f"使能失败：{detail}")

        ok, detail = _run_go_datum_steps(ctx)
        if not ok:
            return _finish(ctx, False, action, f"回基准点失败：{detail}")

        ok, detail = _run_go_origin_steps(ctx)
        if not ok:
            return _finish(ctx, False, action, f"回原点失败：{detail}")

        return _finish(
            ctx,
            True,
            action,
            "设备初始化完成（已使能、已回基准点、已回原点）。",
        )
    except Exception as exc:  # noqa: BLE001
        return _finish(ctx, False, action, f"设备初始化异常: {exc}")


def system_run_initialize(
    ctx: SolidDoserSystemContext,
    *,
    already_begun: bool = False,
) -> ActionResult:
    """空闲 → 初始化中 → 空闲。

    须已回原点。分度盘转一圈：每次开到加料位工位 N，扫码枪读的是背对背工位
    N+4，条码记入扫码位工位。某工位扫码超时/无码时记为空并继续；定位失败或中止
    则整表作废。跑完 8 工位后 materials_ok=True，并写入本地缓存供下次启动加载。
    """
    action = "物料初始化"
    refresh_system_flags(ctx)
    if not already_begun:
        if not ctx.system.enabled_ok:
            return _fail(ctx, action, "未使能，请先执行使能。")
        if not ctx.system.datum_ok:
            return _fail(ctx, action, "未回基准点，请先执行回基准点。")
        if not ctx.system.origin_ok:
            return _fail(ctx, action, "未回原点，请先执行回原点。")

        blocked = _begin(ctx, SystemRunState.INITIALIZING, action)
        if blocked is not None:
            return blocked

    station_count = cfg.INDEXING_STATION_COUNT
    stations = list(cfg.iter_indexing_stations())
    ctx.system.clear_materials()
    ctx.system.station_materials = {i: "" for i in stations}
    ctx.system.materials_ok = False
    cleared, clear_detail = clear_material_dose_totals()
    if cleared:
        get_action_logger().record(f"物料累计加样量已清零（{clear_detail}）")
    else:
        get_action_logger().record(f"物料累计加样量清零失败：{clear_detail}")
    ctx.system.set_progress(0, station_count, "准备扫码")
    disc = get_indexing_disc()
    velocity = ctx.motion_ctx.motion.axis(cfg.INDEXING_AXIS_KEY).velocity

    def _fail_init(msg: str) -> ActionResult:
        ctx.system.clear_materials()
        return _finish(ctx, False, action, msg)

    try:
        # feed_station：开到加料位的工位；scan_station：此时扫码枪对准的工位
        for done, feed_station in enumerate(stations):
            scan_station = cfg.indexing_feed_station_to_scan_station(feed_station)
            if _aborted(ctx):
                return _fail_init("初始化已中止，物料表已作废。")
            ctx.system.set_progress(
                done,
                station_count,
                f"加料位→工位{feed_station}（扫码位=工位{scan_station}）",
            )
            ok, detail = disc.go_to_station(feed_station, velocity)
            ctx.motion_ctx.motion.set_indexing_station(disc.current_station)
            if not ok:
                return _fail_init(f"工位 {feed_station} 定位失败: {detail}")

            if _aborted(ctx):
                return _fail_init("初始化已中止，物料表已作废。")
            ctx.system.set_progress(
                done, station_count, f"扫码工位 {scan_station}"
            )
            ok, detail = scanner_trigger_read(ctx.scanner_ctx)
            barcode = (ctx.scanner_ctx.scanner.last_barcode or "").strip()
            if not ok or not barcode:
                ctx.system.station_materials[scan_station] = ""
                reason = detail or "超时或无码"
                ctx.system.set_progress(
                    done + 1,
                    station_count,
                    f"工位 {scan_station} 无粉盒/未读到（{reason}），继续",
                )
                continue
            ctx.system.station_materials[scan_station] = barcode
            ctx.system.set_progress(
                done + 1,
                station_count,
                f"工位 {scan_station} → {barcode}",
            )

        ctx.system.materials_ok = True
        mapped = "；".join(
            f"{s}:{(c or '—')}"
            for s, c in sorted(ctx.system.station_materials.items())
        )
        barcodes = [
            (c or "").strip()
            for _, c in sorted(ctx.system.station_materials.items())
            if (c or "").strip()
        ]
        reset_ok, reset_detail = reset_material_dose_totals(barcodes)
        if reset_ok:
            get_action_logger().record(f"物料累计表已重建：{reset_detail}")
        else:
            get_action_logger().record(f"物料累计表重建失败：{reset_detail}")
        saved, save_detail = save_station_materials(ctx.system)
        save_note = (
            f"；已本地保存（{save_detail}）"
            if saved
            else f"；本地保存失败: {save_detail}"
        )
        return _finish(ctx, True, action, f"初始化完成。{mapped}{save_note}")
    except Exception as exc:  # noqa: BLE001
        return _fail_init(f"初始化异常: {exc}")


def system_run_dose(
    ctx: SolidDoserSystemContext,
    *,
    hold_for_pickup: bool = False,
    already_begun: bool = False,
) -> ActionResult:
    """空闲（或等待进瓶）→ 加料中 → 空闲（或等待取瓶）。

    输入：dose_items（条码 + 各自重量 mg）。
    前置：物料表有效、条码均在表中、重量>0、设备在原点。
    hold_for_pickup=True：加料成功后进入 WAITING_PICKUP，不清空 host_dose_request_id。
    already_begun=True：调用方已进入 DOSING。
    """
    action = "加料"
    items = list(ctx.system.dose_items or [])
    audit = get_action_logger()

    def _log_dose(msg: str) -> None:
        audit.record(msg)
        _dose_log.info("%s", msg)

    if not ctx.system.materials_ok:
        msg = "未完成物料初始化，请先执行物料初始化。"
        _log_dose(f"加料失败：{msg}")
        return _fail(ctx, action, msg)
    if not items:
        msg = "请至少添加一条物料条码及重量（mg）。"
        _log_dose(f"加料失败：{msg}")
        return _fail(ctx, action, msg)

    bad_weight = [
        (it.material_id or "").strip() or f"第{i}行"
        for i, it in enumerate(items, start=1)
        if float(it.weight_mg or 0.0) <= 0
    ]
    if bad_weight:
        msg = "加料重量必须大于 0（单位 mg）：" + ", ".join(bad_weight)
        _log_dose(f"加料失败：{msg}")
        return _fail(ctx, action, msg)

    empty_code = any(not (it.material_id or "").strip() for it in items)
    if empty_code:
        msg = "存在空的物料条码，请填写完整。"
        _log_dose(f"加料失败：{msg}")
        return _fail(ctx, action, msg)

    refresh_system_flags(ctx)
    if not ctx.system.enabled_ok:
        msg = "未使能，请先执行使能。"
        _log_dose(f"加料失败：{msg}")
        return _fail(ctx, action, msg)
    if not ctx.system.origin_ok:
        msg = "设备未在原点，请先回原点。"
        _log_dose(f"加料失败：{msg}")
        return _fail(ctx, action, msg)

    jobs: list[tuple[str, int, float]] = []
    missing: list[str] = []
    for it in items:
        mid = (it.material_id or "").strip()
        weight_mg = float(it.weight_mg or 0.0)
        station = ctx.system.find_station_by_material(mid)
        if station is None:
            missing.append(mid)
        else:
            jobs.append((mid, station, weight_mg))
    if missing:
        msg = "物料条码无效或不在物料表中：" + ", ".join(missing)
        _log_dose(f"加料失败：{msg}")
        return _fail(ctx, action, msg)

    if not already_begun:
        from_states: Sequence[SystemRunState] = (
            (SystemRunState.WAITING_BOTTLE, SystemRunState.IDLE)
            if hold_for_pickup
            else (SystemRunState.IDLE,)
        )
        blocked = _begin(
            ctx, SystemRunState.DOSING, action, from_states=from_states
        )
        if blocked is not None:
            _log_dose(f"加料失败：{blocked[1]}")
            return blocked

    job_desc = "；".join(f"{m}@工位{s}/{w:g}mg" for m, s, w in jobs)
    _log_dose(f"加料开始：{job_desc}（天平闭环读重）")
    ctx.system.clear_dose_live()

    steps_per_mat = 8
    total = 1 + len(jobs) * steps_per_mat + 1
    disc = get_indexing_disc()
    indexing_vel = ctx.motion_ctx.motion.axis(cfg.INDEXING_AXIS_KEY).velocity
    progress = 0
    balance = get_balance_driver()
    actual_results: list[tuple[str, float, Optional[float]]] = []

    def _safe_stop_actuators() -> None:
        motion.motion_do_set(ctx.motion_ctx, "vibration", False)
        motion.motion_axis_stop(ctx.motion_ctx, _STIR_AXIS_KEY)

    def _fail_dose(msg: str) -> ActionResult:
        _safe_stop_actuators()
        try:
            balance.close_session()
        except Exception:  # noqa: BLE001
            pass
        _log_dose(f"加料失败：{job_desc}，原因={msg}")
        return _finish(ctx, False, action, msg)

    def _step(detail: str) -> None:
        nonlocal progress
        progress += 1
        ctx.system.set_progress(progress, total, detail)

    try:
        ok, detail = balance.open_session()
        if not ok:
            return _fail_dose(f"天平连接失败：{detail}")

        if _aborted(ctx):
            return _fail_dose("加料已停止。")
        _step("水平电机 → 工作位置")
        ok, detail = motion.motion_horizontal_go_named(ctx.motion_ctx, "work")
        if not ok:
            return _fail_dose(detail)

        for idx, (material_id, station, weight_mg) in enumerate(jobs, start=1):
            prefix = f"[{idx}/{len(jobs)}] {material_id}"

            if _aborted(ctx):
                return _fail_dose("加料已停止。")
            _step(f"{prefix} 分度盘 → 工位 {station}")
            ok, detail = disc.go_to_station(station, indexing_vel)
            ctx.motion_ctx.motion.set_indexing_station(disc.current_station)
            if not ok:
                return _fail_dose(detail)

            if _aborted(ctx):
                return _fail_dose("加料已停止。")
            _step(f"{prefix} 振动启动")
            ok, detail = motion.motion_do_set(ctx.motion_ctx, "vibration", True)
            if not ok:
                return _fail_dose(detail)

            if _aborted(ctx):
                return _fail_dose("加料已停止。")
            _step(f"{prefix} 搅拌启动")
            ok, detail = motion.motion_axis_move_abs(ctx.motion_ctx, _STIR_AXIS_KEY)
            if not ok:
                return _fail_dose(detail)

            if _aborted(ctx):
                return _fail_dose("加料已停止。")
            _step(f"{prefix} 升降电机 → 工作位置")
            ok, detail = motion.motion_lift_go_named(ctx.motion_ctx, "work")
            if not ok:
                return _fail_dose(detail)

            if _aborted(ctx):
                return _fail_dose("加料已停止。")
            _step(f"{prefix} 加料中（目标 {weight_mg:g} mg，天平闭环）")
            ctx.system.set_dose_live(
                barcode=material_id,
                target_mg=weight_mg,
                actual_mg=0.0,
            )

            def _on_progress(
                reading,
                delta_mg: float,
                p=prefix,
                tgt=weight_mg,
                mid=material_id,
            ) -> None:
                ctx.system.progress_detail = (
                    f"{p} 加料中 {delta_mg:g}/{tgt:g} mg · {reading.display}"
                )
                ctx.system.set_dose_live(
                    barcode=mid,
                    target_mg=tgt,
                    actual_mg=delta_mg,
                )
                ctx.balance_ctx.balance.connected = True
                ctx.balance_ctx.balance.last_raw = reading.raw
                ctx.balance_ctx.balance.last_weight_value = reading.value
                ctx.balance_ctx.balance.last_weight_unit = reading.unit
                ctx.balance_ctx.balance.last_weight_display = reading.display

            ok, detail, start_mg, end_mg = balance.wait_dose_delta_mg(
                weight_mg,
                max_wait_s=DOSE_MAX_WAIT_S,
                abort_check=lambda: _aborted(ctx),
                on_progress=_on_progress,
            )
            if not ok:
                return _fail_dose(detail)
            _log_dose(f"{prefix} 闭环停料：{detail}")

            if _aborted(ctx):
                return _fail_dose("加料已停止。")
            _step(f"{prefix} 振动停止")
            ok, detail = motion.motion_do_set(ctx.motion_ctx, "vibration", False)
            if not ok:
                return _fail_dose(detail)

            if _aborted(ctx):
                return _fail_dose("加料已停止。")
            _step(f"{prefix} 搅拌停止")
            ok, detail = motion.motion_axis_stop(ctx.motion_ctx, _STIR_AXIS_KEY)
            if not ok:
                return _fail_dose(detail)

            # 停振后稍等再读，得到更接近落定后的实际加料量
            time.sleep(0.8)
            final_reading, _ = balance.read_weight()
            if (
                final_reading is not None
                and final_reading.value_mg is not None
                and start_mg is not None
            ):
                actual_mg = final_reading.value_mg - start_mg
                ctx.balance_ctx.balance.last_raw = final_reading.raw
                ctx.balance_ctx.balance.last_weight_value = final_reading.value
                ctx.balance_ctx.balance.last_weight_unit = final_reading.unit
                ctx.balance_ctx.balance.last_weight_display = final_reading.display
            elif start_mg is not None and end_mg is not None:
                actual_mg = end_mg - start_mg
            else:
                actual_mg = None
            actual_results.append((material_id, weight_mg, actual_mg))
            ctx.system.set_dose_live(
                barcode=material_id,
                target_mg=weight_mg,
                actual_mg=actual_mg,
            )
            # 回写到对应 dose_items，供界面显示实际量
            for it in ctx.system.dose_items:
                if (it.material_id or "").strip() == material_id and float(
                    it.weight_mg or 0.0
                ) == float(weight_mg):
                    it.actual_mg = actual_mg
                    break

            if _aborted(ctx):
                return _fail_dose("加料已停止。")
            _step(f"{prefix} 升降电机 → 原点位置")
            ok, detail = motion.motion_lift_go_named(ctx.motion_ctx, "origin")
            if not ok:
                return _fail_dose(detail)

            if actual_mg is not None:
                _log_dose(
                    f"加料单料完成：物料={material_id}，工位={station}，"
                    f"目标={weight_mg:g} mg，实际={actual_mg:g} mg"
                )
            else:
                _log_dose(
                    f"加料单料完成：物料={material_id}，工位={station}，"
                    f"目标={weight_mg:g} mg，实际=未知"
                )
            ok_save, save_detail = save_dose_record(
                barcode=material_id,
                target_mg=weight_mg,
                actual_mg=actual_mg,
                station=station,
            )
            if ok_save:
                _log_dose(f"加样记录已保存：{material_id} @ {save_detail}")
            else:
                _log_dose(f"加样记录保存失败：{save_detail}")
            if actual_mg is not None:
                ok_tot, tot_detail = add_actual_mg(material_id, actual_mg)
                if ok_tot:
                    _log_dose(f"物料累计已更新：{material_id} +{actual_mg:g} mg")
                else:
                    _log_dose(f"物料累计更新失败：{tot_detail}")

        if _aborted(ctx):
            return _fail_dose("加料已停止。")
        _step("水平电机 → 原点位置")
        ok, detail = motion.motion_horizontal_go_named(ctx.motion_ctx, "origin")
        if not ok:
            return _fail_dose(detail)

        parts = []
        for mid, tgt, act in actual_results:
            if act is None:
                parts.append(f"{mid}:目标{tgt:g}mg/实际—")
            else:
                parts.append(f"{mid}:目标{tgt:g}mg/实际{act:g}mg")
        actual_summary = "；".join(parts) if parts else job_desc
        done_msg = f"加料完成。{actual_summary}"
        _log_dose(f"加料成功：{done_msg}")
        if hold_for_pickup:
            ctx.system.set_progress(0, 1, "等待设备取瓶")
            ctx.system.last_message = "加料完成，等待取瓶。"
            return _finish_to(
                ctx,
                True,
                action,
                done_msg,
                SystemRunState.WAITING_PICKUP,
            )
        return _finish(ctx, True, action, done_msg)
    except Exception as exc:  # noqa: BLE001
        return _fail_dose(f"加料异常: {exc}")
    finally:
        try:
            balance.close_session()
        except Exception:  # noqa: BLE001
            pass
