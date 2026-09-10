"""赛多利斯天平调试动作。"""
from __future__ import annotations

from typing import Tuple

from BusinessActions.SartoriusBalance.balance_context import BalanceContext
from Drivers.SartoriusBalance.balance_driver import get_balance_driver
from Drivers.SartoriusBalance.balance_protocol import BalanceReading

ActionResult = Tuple[bool, str]


def _ok(ctx: BalanceContext, action: str, msg: str) -> ActionResult:
    ctx.balance.last_action = action
    ctx.balance.last_message = msg
    ctx.balance.last_success = True
    return True, msg


def _fail(ctx: BalanceContext, action: str, msg: str) -> ActionResult:
    ctx.balance.last_action = action
    ctx.balance.last_message = msg
    ctx.balance.last_success = False
    return False, msg


def _apply_reading(ctx: BalanceContext, reading: BalanceReading) -> None:
    ctx.balance.last_raw = reading.raw
    ctx.balance.last_weight_value = reading.value
    ctx.balance.last_weight_unit = reading.unit
    ctx.balance.last_weight_display = reading.display or reading.raw
    if reading.display or reading.raw:
        ctx.balance.append_history(reading.display or reading.raw)


def balance_test_connection(ctx: BalanceContext) -> ActionResult:
    action = "天平连接测试"
    driver = get_balance_driver()
    ctx.balance.simulation_mode = driver.simulation
    ok, detail = driver.test_connection()
    ctx.balance.connected = ok
    if ok:
        reading, _ = driver.read_weight()
        if reading is not None:
            _apply_reading(ctx, reading)
        return _ok(ctx, action, detail)
    return _fail(ctx, action, detail)


def balance_read_weight(ctx: BalanceContext) -> ActionResult:
    action = "读取重量"
    driver = get_balance_driver()
    ctx.balance.simulation_mode = driver.simulation
    if not driver.session_open:
        ok, detail = driver.open_session()
        if not ok:
            ctx.balance.connected = False
            return _fail(ctx, action, detail)
    reading, err = driver.read_weight()
    if reading is None:
        ctx.balance.connected = False
        return _fail(ctx, action, err or "读重失败")
    ctx.balance.connected = True
    _apply_reading(ctx, reading)
    return _ok(ctx, action, f"重量：{ctx.balance.last_weight_display}")


def balance_tare(ctx: BalanceContext) -> ActionResult:
    action = "去皮"
    driver = get_balance_driver()
    ctx.balance.simulation_mode = driver.simulation
    reading, err = driver.tare()
    if reading is None and err:
        return _fail(ctx, action, err)
    ctx.balance.connected = True
    if reading and (reading.raw or reading.display):
        _apply_reading(ctx, reading)
    return _ok(ctx, action, "已发送去皮（Esc T）")


def balance_zero(ctx: BalanceContext) -> ActionResult:
    action = "清零"
    driver = get_balance_driver()
    ctx.balance.simulation_mode = driver.simulation
    reading, err = driver.zero()
    if reading is None and err:
        return _fail(ctx, action, err)
    ctx.balance.connected = True
    if reading and (reading.raw or reading.display):
        _apply_reading(ctx, reading)
    return _ok(ctx, action, "已发送清零（Esc V）")


def balance_clear_result(ctx: BalanceContext) -> ActionResult:
    action = "清空读重结果"
    ctx.balance.last_weight_display = ""
    ctx.balance.last_weight_value = None
    ctx.balance.last_weight_unit = ""
    ctx.balance.last_raw = ""
    return _ok(ctx, action, "已清空")
