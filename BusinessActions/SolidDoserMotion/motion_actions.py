"""SolidDoser 主运动调试动作。"""
from __future__ import annotations

from typing import Tuple

from BusinessActions.SolidDoserMotion.motion_context import SolidDoserMotionContext
from Drivers.SolidDoserMotion import motion_config as cfg
from Drivers.SolidDoserMotion.indexing_disc import get_indexing_disc
from Drivers.SolidDoserMotion.motion_driver import (
    apply_status_to_state,
    get_motion_driver,
)
from Drivers.SolidDoserMotion.motion_safety import update_indexing_rotation_allowed

ActionResult = Tuple[bool, str]


def _ok(ctx: SolidDoserMotionContext, action: str, msg: str) -> ActionResult:
    ctx.motion.last_action = action
    ctx.motion.last_message = msg
    ctx.motion.last_success = True
    return True, msg


def _fail(ctx: SolidDoserMotionContext, action: str, msg: str) -> ActionResult:
    ctx.motion.last_action = action
    ctx.motion.last_message = msg
    ctx.motion.last_success = False
    return False, msg


def _sync_status(ctx: SolidDoserMotionContext) -> None:
    status, err = get_motion_driver().read_status()
    apply_status_to_state(ctx.motion, status)
    # 分度盘逻辑工位以上位机 IndexingDisc 为准
    ctx.motion.set_indexing_station(get_indexing_disc().current_station)
    update_indexing_rotation_allowed(ctx.motion)
    if err:
        ctx.motion.last_message = err


def _require_indexing_rotation_allowed(
    ctx: SolidDoserMotionContext,
) -> ActionResult | None:
    """分度旋转前检查升降已使能、已回基准且不高于原点位置。"""
    _sync_status(ctx)
    if ctx.motion.indexing_rotation_allowed:
        return None
    lift = ctx.motion.axis(cfg.LIFT_AXIS_KEY)
    axis = cfg.AXIS_BY_KEY[cfg.LIFT_AXIS_KEY]
    origin = cfg.lift_position_mm("origin")
    if not lift.servo_enabled:
        msg = f"{axis.label}未使能，分度电机禁止旋转。"
    elif not lift.datum_ok:
        msg = f"{axis.label}未回基准点，分度电机禁止旋转。"
    else:
        msg = (
            f"{axis.label}高于原点位置（当前 {lift.actual_position:g}{axis.unit}，"
            f"须 ≤ {origin:g}{axis.unit}），分度电机禁止旋转。"
        )
    return _fail(ctx, "分度电机互锁", msg)


def motion_refresh_all(ctx: SolidDoserMotionContext) -> ActionResult:
    action = "刷新全部电机状态"
    status, err = get_motion_driver().read_status()
    apply_status_to_state(ctx.motion, status)
    ctx.motion.set_indexing_station(get_indexing_disc().current_station)
    if err:
        return _fail(ctx, action, err)
    return _ok(ctx, action, ctx.motion.overview_summary())


def _require_servo(
    ctx: SolidDoserMotionContext, axis_key: str, *, sync: bool = True
) -> ActionResult | None:
    """运动类动作前检查是否已使能；未通过时返回失败结果。"""
    if sync:
        _sync_status(ctx)
    axis = cfg.AXIS_BY_KEY[axis_key]
    st = ctx.motion.axis(axis_key)
    if st.servo_enabled:
        return None
    return _fail(ctx, f"{axis.label} 未使能", f"{axis.label} 未使能，请先使能。")


def _require_datum(
    ctx: SolidDoserMotionContext, axis_key: str, *, sync: bool = True
) -> ActionResult | None:
    """定位类动作前检查是否已回基准点；未通过时返回失败结果。"""
    if sync:
        _sync_status(ctx)
    axis = cfg.AXIS_BY_KEY[axis_key]
    st = ctx.motion.axis(axis_key)
    if st.datum_ok:
        return None
    return _fail(ctx, f"{axis.label} 未回基准点", f"{axis.label} 未回基准点，请先执行回基准点。")


def _require_servo_and_datum(
    ctx: SolidDoserMotionContext, axis_key: str
) -> ActionResult | None:
    """定位前依次检查使能与基准点。"""
    blocked = _require_servo(ctx, axis_key)
    if blocked is not None:
        return blocked
    return _require_datum(ctx, axis_key, sync=False)


def motion_axis_servo_on(ctx: SolidDoserMotionContext, axis_key: str) -> ActionResult:
    axis = cfg.AXIS_BY_KEY[axis_key]
    action = f"{axis.label} 使能"
    ok, detail = get_motion_driver().servo_on(axis_key)
    _sync_status(ctx)
    if ok:
        return _ok(ctx, action, detail)
    return _fail(ctx, action, detail)


def motion_axis_go_datum(ctx: SolidDoserMotionContext, axis_key: str) -> ActionResult:
    """通用轴回基准点（datum）；分度轴委托给分度盘.go_datum()。须已使能。"""
    if axis_key == cfg.INDEXING_AXIS_KEY:
        return indexing_disc_go_datum(ctx)

    blocked = _require_servo(ctx, axis_key)
    if blocked is not None:
        return blocked
    axis = cfg.AXIS_BY_KEY[axis_key]
    action = f"{axis.label} 回基准点"
    ok, detail = get_motion_driver().go_datum(axis_key)
    _sync_status(ctx)
    if ok:
        return _ok(ctx, action, detail)
    return _fail(ctx, action, detail)


def motion_horizontal_go_origin(ctx: SolidDoserMotionContext) -> ActionResult:
    """水平电机运行至命名原点位置。须已使能且已回基准点。"""
    return motion_horizontal_go_named(ctx, "origin")


def motion_horizontal_go_named(
    ctx: SolidDoserMotionContext, position_key: str
) -> ActionResult:
    """水平电机运行至命名位置。须已使能且已回基准点。"""
    blocked = _require_servo_and_datum(ctx, cfg.HORIZONTAL_AXIS_KEY)
    if blocked is not None:
        return blocked
    axis = cfg.AXIS_BY_KEY[cfg.HORIZONTAL_AXIS_KEY]
    st = ctx.motion.axis(cfg.HORIZONTAL_AXIS_KEY)
    named = cfg.HORIZONTAL_POSITION_BY_KEY[position_key]
    target = named.position_mm
    action = f"{axis.label} → {named.label}"
    ok, detail = get_motion_driver().move_absolute(
        cfg.HORIZONTAL_AXIS_KEY, target, st.velocity
    )
    _sync_status(ctx)
    if ok:
        return _ok(ctx, action, detail)
    return _fail(ctx, action, detail)


def motion_indexing_go_origin(ctx: SolidDoserMotionContext) -> ActionResult:
    """分度电机运行至命名原点位置（341°，工位 1）。须已使能且已回基准点。"""
    blocked = _require_servo_and_datum(ctx, cfg.INDEXING_AXIS_KEY)
    if blocked is not None:
        return blocked
    blocked = _require_indexing_rotation_allowed(ctx)
    if blocked is not None:
        return blocked
    axis = cfg.AXIS_BY_KEY[cfg.INDEXING_AXIS_KEY]
    st = ctx.motion.axis(cfg.INDEXING_AXIS_KEY)
    target = cfg.indexing_position_deg("origin")
    action = f"{axis.label} 回原点"
    ok, detail = get_motion_driver().move_absolute(
        cfg.INDEXING_AXIS_KEY, target, st.velocity
    )
    if ok:
        station = get_indexing_disc().sync_station_from_target_angle(target)
        detail = f"{detail} · 当前工位 {station}"
    _sync_status(ctx)
    if ok:
        return _ok(ctx, action, detail)
    return _fail(ctx, action, detail)


def motion_indexing_align_nearest_station(ctx: SolidDoserMotionContext) -> ActionResult:
    """分度电机对齐最近逻辑工位：已在分度位则不动，否则转到最近工位。"""
    blocked = _require_servo_and_datum(ctx, cfg.INDEXING_AXIS_KEY)
    if blocked is not None:
        return blocked
    blocked = _require_indexing_rotation_allowed(ctx)
    if blocked is not None:
        return blocked
    _sync_status(ctx)
    axis = cfg.AXIS_BY_KEY[cfg.INDEXING_AXIS_KEY]
    st = ctx.motion.axis(cfg.INDEXING_AXIS_KEY)
    actual = st.actual_position
    station = cfg.indexing_angle_to_station(actual)
    target = cfg.indexing_station_to_angle(station)
    action = f"{axis.label} 对齐工位"

    span = axis.pos_max - axis.pos_min
    tol = cfg.POSITION_MATCH_TOLERANCE
    if span > 0:
        delta = abs(actual - target) % span
        delta = min(delta, span - delta)
        already = delta <= tol
    else:
        already = abs(actual - target) <= tol

    if already:
        get_indexing_disc().bind_station(station)
        _sync_status(ctx)
        return _ok(
            ctx,
            action,
            f"{axis.label} 已在工位 {station}（{target:g}{axis.unit}），无需动作。",
        )

    ok, detail = get_motion_driver().move_absolute(
        cfg.INDEXING_AXIS_KEY, target, st.velocity
    )
    if ok:
        get_indexing_disc().sync_station_from_target_angle(target)
        detail = f"{detail} · 已对齐工位 {station}（{target:g}{axis.unit}）"
    _sync_status(ctx)
    if ok:
        return _ok(ctx, action, detail)
    return _fail(ctx, action, detail)


def motion_lift_go_origin(ctx: SolidDoserMotionContext) -> ActionResult:
    """升降电机运行至命名原点位置。须已使能且已回基准点。"""
    return motion_lift_go_named(ctx, "origin")


def motion_lift_go_named(
    ctx: SolidDoserMotionContext, position_key: str
) -> ActionResult:
    """升降电机运行至命名位置。须已使能且已回基准点。"""
    blocked = _require_servo_and_datum(ctx, cfg.LIFT_AXIS_KEY)
    if blocked is not None:
        return blocked
    axis = cfg.AXIS_BY_KEY[cfg.LIFT_AXIS_KEY]
    st = ctx.motion.axis(cfg.LIFT_AXIS_KEY)
    named = cfg.LIFT_POSITION_BY_KEY[position_key]
    target = cfg.lift_position_mm(position_key)
    action = f"{axis.label} → {named.label}（{target:g} mm）"
    ok, detail = get_motion_driver().move_absolute(
        cfg.LIFT_AXIS_KEY, target, st.velocity
    )
    _sync_status(ctx)
    if ok:
        return _ok(ctx, action, detail)
    return _fail(ctx, action, detail)


def motion_powder_go_origin(ctx: SolidDoserMotionContext) -> ActionResult:
    """承粉电机运行至命名原点位置。须已使能且已回基准点。"""
    return motion_powder_go_named(ctx, "origin")


def motion_powder_go_named(
    ctx: SolidDoserMotionContext, position_key: str
) -> ActionResult:
    """承粉电机运行至命名位置。须已使能且已回基准点。"""
    blocked = _require_servo_and_datum(ctx, cfg.POWDER_AXIS_KEY)
    if blocked is not None:
        return blocked
    axis = cfg.AXIS_BY_KEY[cfg.POWDER_AXIS_KEY]
    st = ctx.motion.axis(cfg.POWDER_AXIS_KEY)
    named = cfg.POWDER_POSITION_BY_KEY[position_key]
    target = named.position_mm
    action = f"{axis.label} → {named.label}"
    ok, detail = get_motion_driver().move_absolute(
        cfg.POWDER_AXIS_KEY, target, st.velocity
    )
    _sync_status(ctx)
    if ok:
        return _ok(ctx, action, detail)
    return _fail(ctx, action, detail)


def indexing_disc_go_datum(ctx: SolidDoserMotionContext) -> ActionResult:
    """分度盘回基准点。须已使能。"""
    blocked = _require_servo(ctx, cfg.INDEXING_AXIS_KEY)
    if blocked is not None:
        return blocked
    blocked = _require_indexing_rotation_allowed(ctx)
    if blocked is not None:
        return blocked
    action = "分度盘回基准点"
    ok, detail = get_indexing_disc().go_datum()
    _sync_status(ctx)
    if ok:
        return _ok(ctx, action, detail)
    return _fail(ctx, action, detail)


def indexing_disc_go_to_station(
    ctx: SolidDoserMotionContext, station: int
) -> ActionResult:
    """分度盘转到指定工位（1～8）。须已使能且已回基准点。"""
    blocked = _require_servo_and_datum(ctx, cfg.INDEXING_AXIS_KEY)
    if blocked is not None:
        return blocked
    blocked = _require_indexing_rotation_allowed(ctx)
    if blocked is not None:
        return blocked
    station = cfg.normalize_indexing_station(station)
    action = f"分度盘 → 工位 {station}"
    velocity = ctx.motion.axis(cfg.INDEXING_AXIS_KEY).velocity
    ok, detail = get_indexing_disc().go_to_station(station, velocity)
    _sync_status(ctx)
    if ok:
        return _ok(ctx, action, detail)
    return _fail(ctx, action, detail)


def indexing_disc_step_forward(ctx: SolidDoserMotionContext) -> ActionResult:
    """分度盘向前一分度。须已使能且已回基准点。"""
    blocked = _require_servo_and_datum(ctx, cfg.INDEXING_AXIS_KEY)
    if blocked is not None:
        return blocked
    blocked = _require_indexing_rotation_allowed(ctx)
    if blocked is not None:
        return blocked
    action = "分度盘向前一分度"
    velocity = ctx.motion.axis(cfg.INDEXING_AXIS_KEY).velocity
    ok, detail = get_indexing_disc().step_forward(velocity)
    _sync_status(ctx)
    if ok:
        return _ok(ctx, action, detail)
    return _fail(ctx, action, detail)


def indexing_disc_step_backward(ctx: SolidDoserMotionContext) -> ActionResult:
    """分度盘向后一分度。须已使能且已回基准点。"""
    blocked = _require_servo_and_datum(ctx, cfg.INDEXING_AXIS_KEY)
    if blocked is not None:
        return blocked
    blocked = _require_indexing_rotation_allowed(ctx)
    if blocked is not None:
        return blocked
    action = "分度盘向后一分度"
    velocity = ctx.motion.axis(cfg.INDEXING_AXIS_KEY).velocity
    ok, detail = get_indexing_disc().step_backward(velocity)
    _sync_status(ctx)
    if ok:
        return _ok(ctx, action, detail)
    return _fail(ctx, action, detail)


def motion_axis_move_abs(ctx: SolidDoserMotionContext, axis_key: str) -> ActionResult:
    axis = cfg.AXIS_BY_KEY[axis_key]
    st = ctx.motion.axis(axis_key)
    action = f"{axis.label} 启动搅拌" if axis.key == "stirring" else f"{axis.label} 绝对定位"
    if axis.key == "stirring":
        blocked = _require_servo(ctx, axis_key)
    else:
        blocked = _require_servo_and_datum(ctx, axis_key)
    if blocked is not None:
        return blocked
    if axis_key == cfg.INDEXING_AXIS_KEY:
        blocked = _require_indexing_rotation_allowed(ctx)
        if blocked is not None:
            return blocked
    ok, detail = get_motion_driver().move_absolute(
        axis_key, st.target_position, st.velocity
    )
    _sync_status(ctx)
    if ok and axis_key == cfg.INDEXING_AXIS_KEY:
        station = get_indexing_disc().sync_station_from_target_angle(st.target_position)
        ctx.motion.set_indexing_station(station)
        detail = f"{detail} · 当前工位 {station}"
    if ok:
        return _ok(ctx, action, detail)
    return _fail(ctx, action, detail)


def motion_axis_stop(ctx: SolidDoserMotionContext, axis_key: str) -> ActionResult:
    axis = cfg.AXIS_BY_KEY[axis_key]
    action = f"{axis.label} 停止"
    ok, detail = get_motion_driver().stop(axis_key)
    _sync_status(ctx)
    if ok:
        return _ok(ctx, action, detail)
    return _fail(ctx, action, detail)


def motion_do_set(ctx: SolidDoserMotionContext, do_key: str, on: bool) -> ActionResult:
    do_item = cfg.DO_BY_KEY[do_key]
    action = f"{do_item.label} {'开启' if on else '关闭'}"
    ok, detail = get_motion_driver().set_do_output(do_key, on)
    _sync_status(ctx)
    if ok:
        return _ok(ctx, action, detail)
    return _fail(ctx, action, detail)
