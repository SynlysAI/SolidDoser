"""宇泰 UT-6801A TCP 透传客户端（串口 ↔ 以太网）。"""
from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Optional, Tuple

from Drivers.SerialServer import ut_6801a_config as cfg

logger = logging.getLogger("soliddoser.serial_server")

Result = Tuple[bool, str]


class Ut6801aTcpClient:
    """作为 TCP Client 连接串口服务器的 TCP Server，原始字节双向透传。

    支持长连接：连接后可持续 write / read_line，供天平 AUTO 输出流使用。
    """

    def __init__(self) -> None:
        self._sock: Optional[socket.socket] = None
        self._lock = threading.RLock()
        self._rx = bytearray()

    def connect(self) -> Result:
        with self._lock:
            self._close_unlocked()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(cfg.CONNECT_TIMEOUT_S)
            try:
                sock.connect((cfg.SERIAL_SERVER_HOST, cfg.SERIAL_SERVER_PORT))
            except OSError as exc:
                sock.close()
                return (
                    False,
                    f"无法连接串口服务器 {cfg.SERIAL_SERVER_HOST}:{cfg.SERIAL_SERVER_PORT}（{exc}）",
                )
            self._sock = sock
            self._rx.clear()
            return True, ""

    def ensure_connected(self) -> Result:
        with self._lock:
            if self._sock is not None:
                return True, ""
        return self.connect()

    def close(self) -> None:
        with self._lock:
            self._close_unlocked()

    def _close_unlocked(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._rx.clear()

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._sock is not None

    def test_connection(self) -> Result:
        ok, detail = self.connect()
        if not ok:
            return False, detail
        self.close()
        return True, f"已连通 {cfg.SERIAL_SERVER_HOST}:{cfg.SERIAL_SERVER_PORT}"

    def write(self, data: bytes) -> Result:
        with self._lock:
            if self._sock is None:
                return False, "串口服务器未连接"
            try:
                self._sock.sendall(data)
            except OSError as exc:
                self._close_unlocked()
                return False, f"发送失败：{exc}"
            return True, ""

    def drain(self, *, timeout_s: float = 0.05) -> None:
        """清空已缓冲/短时到达的接收数据。"""
        with self._lock:
            if self._sock is None:
                return
            self._rx.clear()
            self._sock.settimeout(timeout_s)
            try:
                while True:
                    chunk = self._sock.recv(4096)
                    if not chunk:
                        break
            except (socket.timeout, OSError):
                pass

    def read_line(
        self,
        *,
        timeout_s: float,
        terminator: bytes = b"\n",
        max_bytes: int = 512,
    ) -> Tuple[Optional[bytes], str]:
        """从长连接读取一行（保留内部缓冲，适配 AUTO 连续输出）。"""
        with self._lock:
            if self._sock is None:
                return None, "串口服务器未连接"

            deadline_t = time.monotonic() + max(0.0, timeout_s)
            while True:
                idx = self._rx.find(terminator)
                if idx >= 0:
                    line = bytes(self._rx[: idx + 1])
                    del self._rx[: idx + 1]
                    return line, ""
                if len(self._rx) >= max_bytes:
                    line = bytes(self._rx[:max_bytes])
                    del self._rx[:max_bytes]
                    return line, ""

                remain = deadline_t - time.monotonic()
                if remain <= 0:
                    if self._rx:
                        line = bytes(self._rx)
                        self._rx.clear()
                        return line, ""
                    return None, "读取响应超时"

                assert self._sock is not None
                self._sock.settimeout(remain)
                try:
                    chunk = self._sock.recv(256)
                except socket.timeout:
                    continue
                except OSError as exc:
                    self._close_unlocked()
                    return None, f"读取失败：{exc}"
                if not chunk:
                    self._close_unlocked()
                    return None, "串口连接已断开"
                self._rx.extend(chunk)

    def read_until(
        self, terminator: bytes = b"\r\n", *, max_bytes: int = 256
    ) -> Tuple[Optional[bytes], str]:
        data, err = self.read_line(
            timeout_s=cfg.IO_TIMEOUT_S,
            terminator=terminator[-1:] if terminator else b"\n",
            max_bytes=max_bytes,
        )
        if data is None:
            return None, err
        return data, ""

    def transact(
        self,
        request: bytes,
        *,
        terminator: bytes = b"\r\n",
        keep_open: bool = True,
    ) -> Tuple[Optional[bytes], str]:
        ok, detail = self.ensure_connected()
        if not ok:
            return None, detail
        self.drain(timeout_s=0.02)
        ok, detail = self.write(request)
        if not ok:
            return None, detail
        data, err = self.read_line(
            timeout_s=cfg.IO_TIMEOUT_S,
            terminator=b"\n",
            max_bytes=256,
        )
        if not keep_open:
            self.close()
        if data is None:
            return None, err
        return data, ""
