"""SolidDoser 主运动轴配置：汇川 AM600-CPU1608TN + EtherCAT。

4 台伺服（水平 / 分度 / 升降 / 搅拌）+ 1 台承粉步进，经 EtherCAT 挂 AM600。
上位机经 Modbus TCP 写轴命令字（WORD bit）与目标/速度（REAL），由 PLC 侧 MC 指令驱动 EtherCAT 轴。
上位机按功能轴显示（水平 / 分度 / 升降 / 搅拌 / 承粉），不依赖 EtherCAT 从站顺序。
SoftMotion 轴变量随接线顺序：Axis=分度 / Axis_1=升降 / Axis_2=搅拌 / Axis_3=水平 / Axis_4=承粉。

通讯参数见 Common/PlcConfig.py；点表见
Dependencies/汇川/AM600-CPU1608TN/SOLIDDOSER_MOTION_PLC_INTERFACE.md。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

from Common.PlcConfig import MODBUS_SLAVE_ID, PLC_HOST, PLC_MODEL, PLC_PORT

MOTION_USE_SIMULATION = False

COMMAND_TIMEOUT_S = 120.0
DATUM_TIMEOUT_S = 180.0  # 找外部基准点（PLC CmdHome / MC_Home）超时
POWER_ON_TIMEOUT_S = 15.0
POLL_INTERVAL_S = 0.2
POSITION_MATCH_TOLERANCE = 0.5
# 分度盘「停在工位」判定（°）；比通用容差更严
INDEXING_STATION_MATCH_TOLERANCE_DEG = 0.01

# 轴命令字 Cmd_*_W / 状态字 Sts_*_W 位定义（与 PLC_PRG 一致）
CMD_BIT_POWER = 0
CMD_BIT_HOME = 1
CMD_BIT_MOVE = 2
CMD_BIT_STOP = 3
STS_BIT_POWER_OK = 0
STS_BIT_HOME_DONE = 1
STS_BIT_MOVE_DONE = 2
STS_BIT_HOMED = 3
STS_BIT_MOVING = 4
STS_BIT_ALARM = 5

# AM600 Modbus 保持寄存器地址 = %MW 索引（1:1）。
# %MW230 → HR 230；%MD250（占 MW500/501）→ HR 500。

# 分度盘：8 工位，工位号 1～8（与盘面标签一致），相邻相差 45°。
# 命名原点与工位 1 绑定，均为 INDEXING_STATION1_DEG（81.55°）。
INDEXING_STATION_COUNT = 8
INDEXING_STATION_FIRST = 1
INDEXING_STEP_DEG = 45.0
INDEXING_STATION1_DEG = 81.55
# 加料位与扫码位背对背：加料位停工位 N 时，扫码枪读到的是工位 N+4（模 8）
INDEXING_FEED_TO_SCAN_OFFSET = 4
INDEXING_AXIS_KEY = "indexing"

# 水平轴命名位置（mm）：原点 / 工作
HORIZONTAL_AXIS_KEY = "horizontal"


@dataclass(frozen=True)
class IndexingNamedPosition:
    key: str
    label: str
    position_deg: float


# 分度轴命名位置（°）：原点 = 工位 1 = INDEXING_STATION1_DEG
INDEXING_POSITIONS: Tuple[IndexingNamedPosition, ...] = (
    IndexingNamedPosition("origin", "原点位置", INDEXING_STATION1_DEG),
)

INDEXING_POSITION_BY_KEY: Dict[str, IndexingNamedPosition] = {
    p.key: p for p in INDEXING_POSITIONS
}


def indexing_position_deg(key: str) -> float:
    """按命名位置 key 取目标角度（°）。"""
    if key not in INDEXING_POSITION_BY_KEY:
        raise KeyError(f"未知分度位置: {key}")
    return INDEXING_POSITION_BY_KEY[key].position_deg


@dataclass(frozen=True)
class HorizontalNamedPosition:
    key: str
    label: str
    position_mm: float


HORIZONTAL_POSITIONS: Tuple[HorizontalNamedPosition, ...] = (
    HorizontalNamedPosition("origin", "原点位置", 10.0),
    HorizontalNamedPosition("work", "工作位置", 105.0),
)

HORIZONTAL_POSITION_BY_KEY: Dict[str, HorizontalNamedPosition] = {
    p.key: p for p in HORIZONTAL_POSITIONS
}


def horizontal_position_mm(key: str) -> float:
    """按命名位置 key 取目标行程（mm）。"""
    if key not in HORIZONTAL_POSITION_BY_KEY:
        raise KeyError(f"未知水平位置: {key}")
    return HORIZONTAL_POSITION_BY_KEY[key].position_mm


# 升降轴命名位置（mm）：原点 / 工作
LIFT_AXIS_KEY = "lift"
LIFT_WORK_MM_DEFAULT = 28.0
LIFT_WORK_MM_MIN = 22.0
LIFT_WORK_MM_MAX = 30.0

# 运行时覆盖（启动时从数据库加载；界面修改后更新）
_lift_work_mm: float = LIFT_WORK_MM_DEFAULT


@dataclass(frozen=True)
class LiftNamedPosition:
    key: str
    label: str
    position_mm: float


LIFT_POSITIONS: Tuple[LiftNamedPosition, ...] = (
    LiftNamedPosition("origin", "原点位置", 10.0),
    LiftNamedPosition("work", "工作位置", LIFT_WORK_MM_DEFAULT),
)

LIFT_POSITION_BY_KEY: Dict[str, LiftNamedPosition] = {
    p.key: p for p in LIFT_POSITIONS
}


def clamp_lift_work_mm(value: float) -> float:
    return max(LIFT_WORK_MM_MIN, min(LIFT_WORK_MM_MAX, float(value)))


def get_lift_work_mm() -> float:
    return _lift_work_mm


def set_lift_work_mm(value: float) -> float:
    """设置升降工作位（mm），自动夹到 22～30。"""
    global _lift_work_mm
    _lift_work_mm = clamp_lift_work_mm(value)
    return _lift_work_mm


def lift_position_mm(key: str) -> float:
    """按命名位置 key 取目标行程（mm）。"""
    if key == "work":
        return get_lift_work_mm()
    if key not in LIFT_POSITION_BY_KEY:
        raise KeyError(f"未知升降位置: {key}")
    return LIFT_POSITION_BY_KEY[key].position_mm


# 承粉轴命名位置（°）：原点 / 工作
POWDER_AXIS_KEY = "powder"


@dataclass(frozen=True)
class PowderNamedPosition:
    key: str
    label: str
    position_mm: float


POWDER_POSITIONS: Tuple[PowderNamedPosition, ...] = (
    PowderNamedPosition("origin", "原点位置", 10.0),
    PowderNamedPosition("work", "工作位置", 20.0),
)

POWDER_POSITION_BY_KEY: Dict[str, PowderNamedPosition] = {
    p.key: p for p in POWDER_POSITIONS
}


def powder_position_mm(key: str) -> float:
    """按命名位置 key 取目标角度（°）。"""
    if key not in POWDER_POSITION_BY_KEY:
        raise KeyError(f"未知承粉位置: {key}")
    return POWDER_POSITION_BY_KEY[key].position_mm


def iter_indexing_stations() -> range:
    """工位号序列：1～8。"""
    return range(
        INDEXING_STATION_FIRST,
        INDEXING_STATION_FIRST + INDEXING_STATION_COUNT,
    )


def indexing_station_to_angle(station: int) -> float:
    """工位号（1～8）→ 目标角度（°）。工位 1 = INDEXING_STATION1_DEG。"""
    s = normalize_indexing_station(station)
    return INDEXING_STATION1_DEG + (s - INDEXING_STATION_FIRST) * INDEXING_STEP_DEG


def indexing_angle_to_station(angle_deg: float) -> int:
    """角度 → 最近工位号（1～8），用于绝对定位后同步逻辑工位。"""
    span = 360.0
    rel = (float(angle_deg) - INDEXING_STATION1_DEG) % span
    if rel < 0:
        rel += span
    idx = int(round(rel / INDEXING_STEP_DEG)) % INDEXING_STATION_COUNT
    if idx < 0:
        idx += INDEXING_STATION_COUNT
    return idx + INDEXING_STATION_FIRST


def indexing_at_any_station(angle_deg: float) -> bool:
    """实际角是否落在任一工位角度（容差 INDEXING_STATION_MATCH_TOLERANCE_DEG，按 360° 最短弧）。"""
    station = indexing_angle_to_station(angle_deg)
    target = indexing_station_to_angle(station)
    span = 360.0
    delta = abs(float(angle_deg) - target) % span
    delta = min(delta, span - delta)
    return delta <= INDEXING_STATION_MATCH_TOLERANCE_DEG


def normalize_indexing_station(station: int) -> int:
    """归一化到工位号 1～8。"""
    s = (int(station) - INDEXING_STATION_FIRST) % INDEXING_STATION_COUNT
    if s < 0:
        s += INDEXING_STATION_COUNT
    return s + INDEXING_STATION_FIRST


def indexing_feed_station_to_scan_station(feed_station: int) -> int:
    """加料位工位 → 当前对准扫码枪的工位（背对背，+4）。"""
    return normalize_indexing_station(
        normalize_indexing_station(feed_station) + INDEXING_FEED_TO_SCAN_OFFSET
    )


def indexing_scan_station_to_feed_station(scan_station: int) -> int:
    """扫码位工位 → 应开到加料位的工位（背对背，-4）。"""
    return normalize_indexing_station(
        normalize_indexing_station(scan_station) - INDEXING_FEED_TO_SCAN_OFFSET
    )


@dataclass(frozen=True)
class AxisMap:
    key: str
    label: str
    motor_type: str
    # AM600 Modbus 线圈写不进 %MX；命令/状态走 D 区 WORD（与 DO 相同通道）
    d_cmd: int  # Cmd_*_W：bit0 Power / bit1 Home / bit2 Move / bit3 Stop
    d_sts: int  # Sts_*_W：bit0 PowerOk / bit1 HomeDone / bit2 MoveDone / bit3 Homed / bit4 Moving / bit5 Alarm
    d_target: int
    d_velocity: int
    d_actual: int
    pos_min: float
    pos_max: float
    vel_default: float
    unit: str


def _axis_block(
    key: str,
    label: str,
    motor_type: str,
    d_target: int,
    d_cmd: int,
    d_sts: int,
    *,
    pos_min: float,
    pos_max: float,
    vel_default: float,
    unit: str,
) -> AxisMap:
    return AxisMap(
        key=key,
        label=label,
        motor_type=motor_type,
        d_cmd=d_cmd,
        d_sts=d_sts,
        d_target=d_target,
        d_velocity=d_target + 2,
        d_actual=d_target + 4,
        pos_min=pos_min,
        pos_max=pos_max,
        vel_default=vel_default,
        unit=unit,
    )


AXES: Tuple[AxisMap, ...] = (
    _axis_block(
        "horizontal",
        "水平电机",
        "SV630N",
        d_target=500,  # %MD250 → MW500
        d_cmd=230,     # %MW230 Cmd_Hori_W
        d_sts=231,     # %MW231 Sts_Hori_W
        pos_min=0.0,
        pos_max=2000.0,
        vel_default=60.0,
        unit="mm",
    ),
    _axis_block(
        "indexing",
        "分度电机",
        "SV630N",
        d_target=530,  # %MD265
        d_cmd=236,
        d_sts=237,
        pos_min=0.0,
        pos_max=360.0,
        vel_default=60.0,
        unit="°",
    ),
    _axis_block(
        "lift",
        "升降电机",
        "SV630N",
        d_target=510,  # %MD255
        d_cmd=232,
        d_sts=233,
        pos_min=0.0,
        pos_max=500.0,
        vel_default=5.0,
        unit="mm",
    ),
    _axis_block(
        "stirring",
        "搅拌电机",
        "SV630N",
        d_target=520,  # %MD260
        d_cmd=234,
        d_sts=235,
        pos_min=0.0,
        pos_max=360.0,
        vel_default=100.0,
        unit="°",
    ),
    _axis_block(
        "powder",
        "承粉电机",
        "STF05-ECX-H",
        d_target=540,  # %MD270
        d_cmd=238,
        d_sts=239,
        pos_min=0.0,
        pos_max=200.0,
        vel_default=60.0,
        unit="°",
    ),
)

AXIS_BY_KEY: Dict[str, AxisMap] = {axis.key: axis for axis in AXES}


@dataclass(frozen=True)
class DoOutputMap:
    key: str
    label: str
    d_register: int  # D 区保持寄存器，1=开 0=关
    q_name: str


# DO：保持寄存器地址 = %MW 索引（AM600）；%MW245 → 245
DO_OUTPUTS: Tuple[DoOutputMap, ...] = (
    DoOutputMap("vibration", "振动电机", 245, "Q0.7"),
    DoOutputMap("ion_fan", "离子风扇", 246, "Q0.6"),
)

DO_BY_KEY: Dict[str, DoOutputMap] = {item.key: item for item in DO_OUTPUTS}


@dataclass(frozen=True)
class DiInputMap:
    key: str
    label: str
    x_address: int  # AM600 X 点，功能码 02：X0=0, X6=6, X8=8, XA=10
    x_name: str     # 丝印名，如 "Xn6"


# DI 输入：试剂瓶有无传感器，接 AM600 Xn6 / Xn8 / XnA
DI_INPUTS: Tuple[DiInputMap, ...] = (
    DiInputMap("bottle_1", "试剂瓶1", 6, "Xn6"),
    DiInputMap("bottle_2", "试剂瓶2", 8, "Xn8"),
    DiInputMap("bottle_3", "试剂瓶3", 10, "XnA"),
)

DI_BY_KEY: Dict[str, DiInputMap] = {item.key: item for item in DI_INPUTS}
