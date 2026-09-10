"""赛多利斯 MCE524S 天平驱动（经 UT-6801A TCP→RS232 透传）。"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional, Tuple

from Drivers.SartoriusBalance import balance_config as cfg
from Drivers.SartoriusBalance.balance_protocol import BalanceReading, parse_sbi_line
from Drivers.SerialServer.ut_6801a_config import (
    SERIAL_SERVER_HOST,
    SERIAL_SERVER_PORT,
)
from Drivers.SerialServer.ut_6801a_transport import Ut6801aTcpClient

logger = logging.getLogger("soliddoser.balance")

Result = Tuple[bool, str]
ReadResult = Tuple[Optional[BalanceReading], str]
AbortCheck = Callable[[], bool]
ProgressCb = Callable[[BalanceReading, float], None]

_driver: Optional["SartoriusBalanceDriver"] = None


class SartoriusBalanceDriver:
    def __init__(self) -> None:
        self._simulation = self._resolve_simulation_mode()
        self._client = Ut6801aTcpClient()
        self._session_open = False
        self._sim_weight_g = 0.0

    @property
    def simulation(self) -> bool:
        return self._simulation

    @property
    def session_open(self) -> bool:
        return self._session_open or self._simulation

    @staticmethod
    def _resolve_simulation_mode() -> bool:
        if cfg.BALANCE_USE_SIMULATION:
            return True
        client = Ut6801aTcpClient()
        ok, _ = client.test_connection()
        if ok:
            return False
        logger.warning(
            "无法连接串口服务器 %s:%s（天平 %s），使用仿真模式",
            SERIAL_SERVER_HOST,
            SERIAL_SERVER_PORT,
            cfg.BALANCE_MODEL,
        )
        return True

    def open_session(self) -> Result:
        if self._simulation:
            self._session_open = True
            return True, "仿真模式已就绪"
        ok, detail = self._client.ensure_connected()
        if not ok:
            self._session_open = False
            return False, detail
        self._client.drain(timeout_s=0.05)
        self._session_open = True
        return True, f"已长连接 {SERIAL_SERVER_HOST}:{SERIAL_SERVER_PORT}"

    def close_session(self) -> None:
        self._session_open = False
        if not self._simulation:
            self._client.close()

    def test_connection(self) -> Result:
        if self._simulation:
            return (
                True,
                f"仿真模式（{SERIAL_SERVER_HOST}:{SERIAL_SERVER_PORT} · {cfg.BALANCE_MODEL}）",
            )
        ok, detail = self.open_session()
        if not ok:
            return False, detail
        reading, err = self.read_weight()
        if reading is not None and reading.value is not None:
            return True, f"TCP+SBI 正常 · {reading.display}"
        # AUTO 流偶发空窗时再试 Esc P
        reading, err = self._command(cfg.CMD_PRINT, expect_reply=True)
        if reading is not None and not err:
            return True, f"TCP+SBI 正常 · {reading.display or reading.raw}"
        return False, self._sbi_timeout_hint(err or "天平无应答")

    @staticmethod
    def _sbi_timeout_hint(err: str) -> str:
        if "超时" not in err and "空" not in err and "无应答" not in err:
            return err
        from Drivers.SerialServer.ut_6801a_config import SERIAL_PARAMS_HINT

        return (
            f"{err}（TCP 已通，天平无 SBI 回包）。"
            f"请核对 UT-6801A Web 与天平 DEVICE→COM-RS232：{SERIAL_PARAMS_HINT}；"
            f"本机现场线缆为交叉线；DAT.REC.=SBI；COM.OUTP.=AUTO W/O"
        )

    def _command(self, cmd: bytes, *, expect_reply: bool) -> ReadResult:
        if self._simulation:
            if expect_reply:
                reading, _ = parse_sbi_line(f"+    {self._sim_weight_g:8.4f} g")
                return reading, ""
            return (
                BalanceReading(
                    raw="", value=None, unit="", id_code="", display="OK", stable=True
                ),
                "",
            )

        if not self._session_open:
            ok, detail = self.open_session()
            if not ok:
                return None, detail

        raw, err = self._client.transact(cmd, keep_open=True)
        if not expect_reply:
            if err and "超时" in err:
                return (
                    BalanceReading(
                        raw="",
                        value=None,
                        unit="",
                        id_code="",
                        display="已发送",
                        stable=True,
                    ),
                    "",
                )
            if raw is None and err:
                return None, err
            reading, parse_err = parse_sbi_line(raw or b"")
            if reading is None and parse_err and raw:
                return (
                    BalanceReading(
                        raw=raw.decode("ascii", errors="replace").strip(),
                        value=None,
                        unit="",
                        id_code="",
                        display="已发送",
                        stable=True,
                    ),
                    "",
                )
            return reading, parse_err

        if raw is None:
            return None, self._sbi_timeout_hint(err or "读重失败")
        return parse_sbi_line(raw)

    def read_next_stream(
        self, *, timeout_s: float = cfg.STREAM_LINE_TIMEOUT_S
    ) -> ReadResult:
        """读取下一行自动重量（AUTO 模式）；超时返回错误。"""
        if self._simulation:
            self._sim_weight_g += 0.002
            return parse_sbi_line(f"N     +  {self._sim_weight_g:8.4f} g")

        if not self._session_open:
            ok, detail = self.open_session()
            if not ok:
                return None, detail

        raw, err = self._client.read_line(timeout_s=timeout_s)
        if raw is None:
            return None, err
        return parse_sbi_line(raw)

    def read_weight(self) -> ReadResult:
        """优先收 AUTO 流；短时无数据时再发 Esc P。"""
        reading, err = self.read_next_stream(timeout_s=cfg.STREAM_LINE_TIMEOUT_S)
        if reading is not None and reading.value is not None:
            return reading, ""
        return self._command(cfg.CMD_PRINT, expect_reply=True)

    def tare(self) -> ReadResult:
        return self._command(cfg.CMD_TARE, expect_reply=False)

    def zero(self) -> ReadResult:
        return self._command(cfg.CMD_ZERO, expect_reply=False)

    def wait_dose_delta_mg(
        self,
        target_mg: float,
        *,
        max_wait_s: float = cfg.DOSE_MAX_WAIT_S,
        early_stop_mg: float = cfg.DOSE_EARLY_STOP_MG,
        abort_check: Optional[AbortCheck] = None,
        on_progress: Optional[ProgressCb] = None,
    ) -> Tuple[bool, str, Optional[float], Optional[float]]:
        """从当前重量起，净增量达到 target_mg（可减 early_stop）即成功。

        返回：(ok, msg, start_mg, end_mg)；失败时 end_mg 可能为已达到的净重终点。
        """
        if target_mg <= 0:
            return False, "目标重量必须大于 0 mg", None, None

        ok, detail = self.open_session()
        if not ok:
            return False, detail, None, None

        start, err = self.read_weight()
        if start is None or start.value is None:
            return False, err or "无法读取加料起点重量", None, None
        start_mg = start.value_mg
        if start_mg is None:
            return False, f"无法换算起点重量单位：{start.raw}", None, None

        threshold_mg = max(0.0, target_mg - max(0.0, early_stop_mg))
        deadline = time.monotonic() + max_wait_s
        last = start
        end_mg = start_mg

        while True:
            if abort_check and abort_check():
                return False, "加料已停止。", start_mg, end_mg
            if time.monotonic() >= deadline:
                cur_mg = last.value_mg if last.value_mg is not None else float("nan")
                return (
                    False,
                    f"加料超时（目标 {target_mg:g} mg，已约 {cur_mg - start_mg:g} mg）",
                    start_mg,
                    end_mg,
                )

            reading, err = self.read_next_stream(timeout_s=cfg.STREAM_LINE_TIMEOUT_S)
            if reading is None or reading.value is None:
                reading, err = self._command(cfg.CMD_PRINT, expect_reply=True)
                if reading is None or reading.value is None:
                    continue
            last = reading
            cur_mg = reading.value_mg
            if cur_mg is None:
                continue
            end_mg = cur_mg
            delta_mg = cur_mg - start_mg
            if on_progress is not None:
                on_progress(reading, delta_mg)
            if delta_mg >= threshold_mg:
                return (
                    True,
                    f"已达停料阈值：净增 {delta_mg:g} mg / 目标 {target_mg:g} mg"
                    f"（当前 {reading.display}）",
                    start_mg,
                    end_mg,
                )


def get_balance_driver() -> SartoriusBalanceDriver:
    global _driver
    if _driver is None:
        _driver = SartoriusBalanceDriver()
    return _driver
