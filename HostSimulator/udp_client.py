"""UDP 客户端：发命令到加样 8890，监听本机 8891 事件。"""
from __future__ import annotations

import json
import socket
import threading
import uuid
from typing import Callable, Optional

from Common.HostBottleFeedConfig import (
    BOTTLE_FEED_UDP_HOST,
    BOTTLE_FEED_UDP_PORT,
    HOST_MAIN_UDP_HOST,
    HOST_MAIN_UDP_PORT,
)


class HostUdpClient:
    def __init__(
        self,
        *,
        sd_host: str = BOTTLE_FEED_UDP_HOST,
        sd_port: int = BOTTLE_FEED_UDP_PORT,
        listen_host: str = HOST_MAIN_UDP_HOST,
        listen_port: int = HOST_MAIN_UDP_PORT,
        timeout_s: float = 5.0,
    ):
        self.sd_host = sd_host
        self.sd_port = sd_port
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.timeout_s = timeout_s
        self._listen_sock: Optional[socket.socket] = None
        self._listen_thread: Optional[threading.Thread] = None
        self._running = False
        self._on_event: Optional[Callable[[dict], None]] = None
        self._on_log: Optional[Callable[[str], None]] = None

    def set_handlers(
        self,
        *,
        on_event: Optional[Callable[[dict], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._on_event = on_event
        self._on_log = on_log

    def _log(self, text: str) -> None:
        if self._on_log is not None:
            self._on_log(text)

    def start_listen(self) -> None:
        if self._running:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((self.listen_host, self.listen_port))
        self._listen_sock = sock
        self._running = True
        self._listen_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._listen_thread.start()
        self._log(f"监听设备端口 {self.listen_host}:{self.listen_port}")

    def stop_listen(self) -> None:
        self._running = False
        if self._listen_sock is not None:
            try:
                self._listen_sock.close()
            except OSError:
                pass
            self._listen_sock = None
        if self._listen_thread is not None and self._listen_thread.is_alive():
            self._listen_thread.join(timeout=1.0)
        self._listen_thread = None

    def _listen_loop(self) -> None:
        assert self._listen_sock is not None
        while self._running:
            try:
                data, addr = self._listen_sock.recvfrom(65535)
            except OSError:
                break
            raw = data.decode("utf-8", errors="replace")
            self._log(f"← 事件 from {addr[0]}:{addr[1]} {raw}")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and self._on_event is not None:
                self._on_event(payload)

    def send_cmd(self, payload: dict) -> dict:
        if "request_id" not in payload or not payload.get("request_id"):
            payload = {**payload, "request_id": str(uuid.uuid4())}
        raw = json.dumps(payload, ensure_ascii=False)
        self._log(f"→ 命令 {self.sd_host}:{self.sd_port} {raw}")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(self.timeout_s)
            sock.sendto(raw.encode("utf-8"), (self.sd_host, self.sd_port))
            try:
                data, _addr = sock.recvfrom(65535)
            except socket.timeout:
                err = {
                    "cmd": payload.get("cmd"),
                    "request_id": payload.get("request_id"),
                    "status": "error",
                    "reason": "timeout",
                    "message": f"等待加样回执超时（{self.timeout_s:g}s）",
                }
                self._log(f"← 回执超时 {err}")
                return err
            text = data.decode("utf-8", errors="replace")
            self._log(f"← 回执 {text}")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {
                    "cmd": payload.get("cmd"),
                    "status": "error",
                    "message": f"非 JSON 回执：{text}",
                }

    @staticmethod
    def new_request_id() -> str:
        return str(uuid.uuid4())
