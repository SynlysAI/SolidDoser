"""主机控制 / 试剂瓶 UDP：本机监听 + 向主系统推送事件。"""
from __future__ import annotations

import json
import logging
import socket
import threading
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal

from BusinessActions.SolidDoserSystem.system_actions import (
    dose_items_payload,
    system_bottle_discharge_done,
    system_bottle_feed_done,
    system_bottle_feed_request,
    system_host_begin_bottle_placed,
    system_host_begin_device_init,
    system_host_begin_material_init,
    system_host_bottle_removed,
    system_host_dose_confirm,
    system_host_dose_request,
    system_request_abort,
    system_run_device_init,
    system_run_dose,
    system_run_initialize,
)
from BusinessActions.SolidDoserSystem.system_context import SolidDoserSystemContext
from Common.HostBottleFeedConfig import (
    BOTTLE_FEED_UDP_HOST,
    BOTTLE_FEED_UDP_PORT,
    CMD_ABORT,
    CMD_BOTTLE_DISCHARGE_DONE,
    CMD_BOTTLE_FEED_DONE,
    CMD_BOTTLE_FEED_REQUEST,
    CMD_BOTTLE_PLACED,
    CMD_BOTTLE_REMOVED,
    CMD_DEVICE_INIT_DONE,
    CMD_DEVICE_INIT_REQUEST,
    CMD_DOSE_COMPLETE,
    CMD_DOSE_CONFIRM,
    CMD_DOSE_REQUEST,
    CMD_MATERIAL_INIT_DONE,
    CMD_MATERIAL_INIT_REQUEST,
    HOST_MAIN_UDP_HOST,
    HOST_MAIN_UDP_PORT,
)

logger = logging.getLogger("soliddoser.bottle_feed_udp")

_bridge: Optional["BottleFeedUdpBridge"] = None


def get_bottle_comm() -> "BottleFeedUdpBridge":
    if _bridge is None:
        raise RuntimeError("主机控制 UDP 未启动，请用 main.py / main_soliddoser.py 启动。")
    return _bridge


class BottleFeedUdpBridge(QObject):
    """UDP 线程 → Qt 主线程；处理主机命令并回包 / 推送事件。"""

    command_received = Signal(dict, str, int)  # payload, reply_host, reply_port

    def __init__(self, ctx: SolidDoserSystemContext, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self.command_received.connect(self._on_command)

    def start(self, host: str = BOTTLE_FEED_UDP_HOST, port: int = BOTTLE_FEED_UDP_PORT) -> None:
        if self._running:
            return
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((host, port))
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        logger.info("主机控制 UDP 已监听 %s:%s", host, port)

    def stop(self) -> None:
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        logger.info("主机控制 UDP 已停止")

    def send_to_host(self, message: dict) -> bool:
        """向主系统监听端口发送 JSON（完成事件等）。"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                raw = json.dumps(message, ensure_ascii=False).encode("utf-8")
                sock.sendto(raw, (HOST_MAIN_UDP_HOST, HOST_MAIN_UDP_PORT))
            logger.info(
                "已发往主系统 %s:%s %s",
                HOST_MAIN_UDP_HOST,
                HOST_MAIN_UDP_PORT,
                message,
            )
            return True
        except OSError:
            logger.exception(
                "发往主系统失败 → %s:%s", HOST_MAIN_UDP_HOST, HOST_MAIN_UDP_PORT
            )
            return False

    def _listen_loop(self) -> None:
        assert self._sock is not None
        while self._running:
            try:
                data, addr = self._sock.recvfrom(65535)
            except OSError:
                if self._running:
                    logger.exception("主机控制 UDP 接收失败")
                break
            try:
                raw = data.decode("utf-8").strip()
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError("payload 不是 JSON 对象")
            except Exception as exc:  # noqa: BLE001
                logger.warning("无效主机控制 UDP 报文 from %s: %s", addr, exc)
                self._send_reply(
                    addr[0],
                    addr[1],
                    {
                        "cmd": "UNKNOWN",
                        "status": "error",
                        "reason": "bad_request",
                        "message": f"无效 JSON：{exc}",
                    },
                )
                continue
            self.command_received.emit(payload, addr[0], int(addr[1]))

    def _send_reply(self, host: str, port: int, message: dict) -> None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.sendto(
                    json.dumps(message, ensure_ascii=False).encode("utf-8"),
                    (host, port),
                )
        except OSError:
            logger.exception("主机控制 UDP 回包失败 → %s:%s", host, port)

    def _spawn(self, target: Callable[[], None]) -> None:
        threading.Thread(target=target, daemon=True).start()

    def _on_command(self, payload: dict, host: str, port: int) -> None:
        cmd = str(payload.get("cmd") or payload.get("command") or "").strip()
        request_id = str(payload.get("request_id") or "")

        # —— 旧瓶进/出料 ——
        if cmd == CMD_BOTTLE_FEED_REQUEST:
            _, _, resp = system_bottle_feed_request(self._ctx, request_id)
            self._send_reply(host, port, resp)
            return
        if cmd == CMD_BOTTLE_FEED_DONE:
            _, _, resp = system_bottle_feed_done(self._ctx, request_id)
            self._send_reply(host, port, resp)
            return
        if cmd == CMD_BOTTLE_DISCHARGE_DONE:
            _, _, resp = system_bottle_discharge_done(self._ctx, request_id)
            self._send_reply(host, port, resp)
            return

        # —— 联机：设备初始化 ——
        if cmd == CMD_DEVICE_INIT_REQUEST:
            ok, _msg, resp = system_host_begin_device_init(self._ctx, request_id)
            self._send_reply(host, port, resp)
            if not ok:
                return

            def _work() -> None:
                result_ok, result_msg = system_run_device_init(
                    self._ctx, already_begun=True
                )
                self.send_to_host(
                    {
                        "cmd": CMD_DEVICE_INIT_DONE,
                        "request_id": request_id,
                        "status": "success" if result_ok else "error",
                        "message": result_msg,
                    }
                )

            self._spawn(_work)
            return

        # —— 联机：物料初始化 ——
        if cmd == CMD_MATERIAL_INIT_REQUEST:
            ok, _msg, resp = system_host_begin_material_init(self._ctx, request_id)
            self._send_reply(host, port, resp)
            if not ok:
                return

            def _work() -> None:
                result_ok, result_msg = system_run_initialize(
                    self._ctx, already_begun=True
                )
                self.send_to_host(
                    {
                        "cmd": CMD_MATERIAL_INIT_DONE,
                        "request_id": request_id,
                        "status": "success" if result_ok else "error",
                        "message": result_msg,
                        "materials_ok": self._ctx.system.materials_ok,
                        "station_materials": {
                            str(k): v
                            for k, v in (
                                self._ctx.system.station_materials or {}
                            ).items()
                        },
                    }
                )

            self._spawn(_work)
            return

        # —— 联机：加料申请 / 确认 / 放瓶 / 取瓶 ——
        if cmd == CMD_DOSE_REQUEST:
            items = payload.get("items") or []
            _, _, resp = system_host_dose_request(self._ctx, items, request_id)
            self._send_reply(host, port, resp)
            return

        if cmd == CMD_DOSE_CONFIRM:
            _, _, resp = system_host_dose_confirm(self._ctx, request_id)
            self._send_reply(host, port, resp)
            return

        if cmd == CMD_BOTTLE_PLACED:
            ok, _msg, resp = system_host_begin_bottle_placed(self._ctx, request_id)
            self._send_reply(host, port, resp)
            if not ok:
                return

            rid = request_id or self._ctx.system.host_dose_request_id

            def _work() -> None:
                result_ok, result_msg = system_run_dose(
                    self._ctx,
                    hold_for_pickup=True,
                    already_begun=True,
                )
                event = {
                    "cmd": CMD_DOSE_COMPLETE,
                    "request_id": rid,
                    "status": "success" if result_ok else "error",
                    "message": result_msg,
                    "items": dose_items_payload(self._ctx),
                    "state": (
                        "waiting_pickup"
                        if result_ok
                        else self._ctx.system.run_state.name.lower()
                    ),
                }
                self.send_to_host(event)

            self._spawn(_work)
            return

        if cmd == CMD_BOTTLE_REMOVED:
            _, _, resp = system_host_bottle_removed(self._ctx, request_id)
            self._send_reply(host, port, resp)
            return

        if cmd == CMD_ABORT:
            ok, msg = system_request_abort(self._ctx)
            self._send_reply(
                host,
                port,
                {
                    "cmd": CMD_ABORT,
                    "request_id": request_id,
                    "status": "success" if ok else "error",
                    "message": msg,
                },
            )
            return

        self._send_reply(
            host,
            port,
            {
                "cmd": cmd or "UNKNOWN",
                "status": "error",
                "reason": "unknown_cmd",
                "message": f"未知命令：{cmd}",
                "request_id": request_id,
            },
        )


def start_bottle_feed_udp(ctx: SolidDoserSystemContext) -> BottleFeedUdpBridge:
    global _bridge
    bridge = BottleFeedUdpBridge(ctx)
    bridge.start()
    _bridge = bridge
    return bridge
