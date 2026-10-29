"""分度盘：逻辑上挂在分度电机之上，用工位号驱动电机。

模型：
    分度盘 (IndexingDisc)
        └── 分度电机 (motion_driver / indexing axis)

约定：go_to_station(N) / step 把工位 N 开到加料位（与调试「分度」一致）。
扫码枪与加料位背对背，加料位停 N 时扫码位对准 N+4（见 motion_config）。

工位 1～8（与盘面标签一致），相邻 45°；命名原点与工位 1 均为 341°。
步进只改逻辑工位，再换算成整角度绝对定位，不读取实际位置的小数，避免误差累积。
"""
from __future__ import annotations

from typing import Optional, Tuple

from Drivers.SolidDoserMotion import motion_config as cfg
from Drivers.SolidDoserMotion.motion_driver import (
    SolidDoserMotionDriver,
    get_motion_driver,
)
from Drivers.SolidDoserMotion.motion_safety import check_indexing_rotation_allowed

Result = Tuple[bool, str]

_disc: Optional["IndexingDisc"] = None


class IndexingDisc:
    """分度盘设备对象。

    对外只暴露盘级动作；电机使能/停止仍由运动驱动按轴操作。
    """

    def __init__(self, driver: Optional[SolidDoserMotionDriver] = None) -> None:
        self._driver = driver or get_motion_driver()
        self._axis_key = cfg.INDEXING_AXIS_KEY
        self._station = cfg.INDEXING_STATION_FIRST

    @property
    def station_count(self) -> int:
        return cfg.INDEXING_STATION_COUNT

    @property
    def step_deg(self) -> float:
        return cfg.INDEXING_STEP_DEG

    @property
    def current_station(self) -> int:
        """当前逻辑工位（1～8）。"""
        return self._station

    @property
    def current_angle_cmd(self) -> float:
        """当前工位对应的指令角度（°）。"""
        return cfg.indexing_station_to_angle(self._station)

    def go_datum(self) -> Result:
        """分度盘回基准点：找外部基准；成功后逻辑工位归 1（与命名原点绑定）。"""
        ok, detail = self._ensure_rotation_allowed()
        if not ok:
            return False, detail
        ok, detail = self._driver.go_datum(self._axis_key)
        if not ok:
            return False, detail
        self._station = cfg.INDEXING_STATION_FIRST
        origin_deg = cfg.indexing_position_deg("origin")
        return True, (
            f"分度盘已回基准点（工位 {self._station} / 原点 {origin_deg:g}°）。"
            f"{detail}"
        )

    def step_forward(self, velocity: float) -> Result:
        """向前一分度：转到下一工位（工位 +1，绕回 1～8）。"""
        return self._step(+1, velocity)

    def step_backward(self, velocity: float) -> Result:
        """向后一分度：转到上一工位（工位 -1，绕回 1～8）。"""
        return self._step(-1, velocity)

    def go_to_station(self, station: int, velocity: float) -> Result:
        """转到指定工位（1～8）。"""
        station = cfg.normalize_indexing_station(station)
        return self._move_to_station(station, velocity)

    def sync_station_from_target_angle(self, angle_deg: float) -> int:
        """绝对定位后，按目标角同步逻辑工位（供调试「定位」使用）。"""
        self._station = cfg.indexing_angle_to_station(angle_deg)
        return self._station

    def bind_station(self, station: int) -> None:
        """仅同步逻辑工位，不驱动电机（状态恢复用）。"""
        self._station = cfg.normalize_indexing_station(station)

    def _ensure_rotation_allowed(self) -> Result:
        status, err = self._driver.read_status()
        if err:
            return False, err
        return check_indexing_rotation_allowed(status)

    def _step(self, delta: int, velocity: float) -> Result:
        next_station = cfg.normalize_indexing_station(self._station + delta)
        direction = "向前" if delta > 0 else "向后"
        ok, detail = self._move_to_station(next_station, velocity)
        if not ok:
            return False, detail
        return True, (
            f"分度盘{direction}一分度 → 工位 {self._station}"
            f"（{self.current_angle_cmd:g}°）。{detail}"
        )

    def _move_to_station(self, station: int, velocity: float) -> Result:
        ok, detail = self._ensure_rotation_allowed()
        if not ok:
            return False, detail
        target = cfg.indexing_station_to_angle(station)
        ok, detail = self._driver.move_absolute(self._axis_key, target, velocity)
        if not ok:
            return False, detail
        self._station = station
        return True, detail


def get_indexing_disc() -> IndexingDisc:
    global _disc
    if _disc is None:
        _disc = IndexingDisc()
    return _disc
