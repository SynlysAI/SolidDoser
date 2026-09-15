"""设备端模拟主窗口：步骤引导 + 操作 + 加料清单 + 日志。"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from Common.HostBottleFeedConfig import (
    BOTTLE_FEED_UDP_HOST,
    BOTTLE_FEED_UDP_PORT,
    CMD_ABORT,
    CMD_BOTTLE_PLACED,
    CMD_BOTTLE_REMOVED,
    CMD_DEVICE_INIT_DONE,
    CMD_DEVICE_INIT_REQUEST,
    CMD_DOSE_COMPLETE,
    CMD_DOSE_REQUEST,
    CMD_MATERIAL_INIT_DONE,
    CMD_MATERIAL_INIT_REQUEST,
    HOST_MAIN_UDP_HOST,
    HOST_MAIN_UDP_PORT,
)
from HostSimulator.udp_client import HostUdpClient


class SimStep(Enum):
    IDLE = 0
    DEVICE_INIT = 1
    MATERIAL_INIT = 2
    DOSE_APPLY = 3
    WAIT_BOTTLE = 4
    DOSING = 5
    WAIT_PICKUP = 6


_STEP_LABELS = (
    (SimStep.IDLE, "空闲"),
    (SimStep.DEVICE_INIT, "设备初始化"),
    (SimStep.MATERIAL_INIT, "物料初始化"),
    (SimStep.DOSE_APPLY, "申请加料"),
    (SimStep.WAIT_BOTTLE, "等待进瓶"),
    (SimStep.DOSING, "加样中"),
    (SimStep.WAIT_PICKUP, "等待取瓶"),
)

_STYLESHEET = """
QMainWindow, QWidget#Root {
    background: #eef2f7;
}
QFrame#Card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
}
QLabel#Title {
    font-size: 16px;
    font-weight: 700;
    color: #0f172a;
}
QLabel#Hint {
    font-size: 12px;
    color: #64748b;
}
QLabel#StepIdle {
    font-size: 12px;
    color: #94a3b8;
    padding: 6px 10px;
    border: 1px solid #e2e8f0;
    border-radius: 14px;
    background: #f8fafc;
}
QLabel#StepCurrent {
    font-size: 12px;
    font-weight: 700;
    color: #1d4ed8;
    padding: 6px 10px;
    border: 1px solid #93c5fd;
    border-radius: 14px;
    background: #eff6ff;
}
QLabel#StepDone {
    font-size: 12px;
    font-weight: 600;
    color: #047857;
    padding: 6px 10px;
    border: 1px solid #6ee7b7;
    border-radius: 14px;
    background: #ecfdf5;
}
QLabel#StatusValue {
    font-size: 13px;
    font-weight: 600;
    color: #0f172a;
}
QPushButton {
    min-height: 32px;
    padding: 4px 12px;
    border: 1px solid #cbd5e1;
    border-radius: 5px;
    background: #ffffff;
    color: #334155;
}
QPushButton:hover { background: #f1f5f9; }
QPushButton:disabled { color: #94a3b8; background: #f8fafc; }
QPushButton#PrimaryBtn {
    border-color: #60a5fa;
    color: #1d4ed8;
    background: #eff6ff;
    font-weight: 700;
}
QPushButton#DangerBtn {
    border-color: #f87171;
    color: #b91c1c;
    background: #fff1f2;
    font-weight: 700;
}
QTableWidget {
    border: 1px solid #e2e8f0;
    gridline-color: #eef2f7;
}
QPlainTextEdit#Log {
    font-family: Consolas, "Courier New", monospace;
    font-size: 12px;
    background: #0f172a;
    color: #e2e8f0;
    border-radius: 6px;
    border: none;
}
"""


class HostSimulatorWindow(QMainWindow):
    event_arrived = Signal(dict)
    log_arrived = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("设备端模拟软件 · 固体加样联机")
        self.resize(980, 720)
        self.setStyleSheet(_STYLESHEET)

        self._step = SimStep.IDLE
        self._request_id = ""
        self._step_labels: List[QLabel] = []
        self._client = HostUdpClient()
        self._client.set_handlers(on_event=self._on_udp_event, on_log=self._on_udp_log)
        self.event_arrived.connect(self._handle_event)
        self.log_arrived.connect(self._append_log)

        self._status_label: Optional[QLabel] = None
        self._request_label: Optional[QLabel] = None
        self._dose_table: Optional[QTableWidget] = None
        self._log_view: Optional[QPlainTextEdit] = None
        self._btn_device_init: Optional[QPushButton] = None
        self._btn_material_init: Optional[QPushButton] = None
        self._btn_dose_request: Optional[QPushButton] = None
        self._btn_dose_confirm: Optional[QPushButton] = None
        self._btn_bottle_placed: Optional[QPushButton] = None
        self._btn_bottle_removed: Optional[QPushButton] = None
        self._btn_abort: Optional[QPushButton] = None

        self._build_ui()
        self._refresh_step_ui()
        self._client.start_listen()
        self._append_log(
            f"已连接设定：命令→{BOTTLE_FEED_UDP_HOST}:{BOTTLE_FEED_UDP_PORT}，"
            f"事件←{HOST_MAIN_UDP_HOST}:{HOST_MAIN_UDP_PORT}"
        )
        self._append_log("请先启动固体加样软件（main.py），再按步骤操作。")

    def closeEvent(self, event) -> None:
        self._client.stop_listen()
        super().closeEvent(event)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title = QLabel("设备端模拟软件")
        title.setObjectName("Title")
        layout.addWidget(title)
        hint = QLabel(
            "模拟主设备软件：驱动固体加样完成设备初始化 → 物料初始化 → 申请加料（加样侧自动响应） → 进瓶 → 加样 → 取瓶。"
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 步骤条
        step_card = QFrame()
        step_card.setObjectName("Card")
        step_layout = QVBoxLayout(step_card)
        step_layout.setContentsMargins(12, 10, 12, 10)
        step_layout.setSpacing(8)
        step_title = QLabel("当前流程步骤")
        step_title.setObjectName("Title")
        step_layout.addWidget(step_title)
        row = QHBoxLayout()
        row.setSpacing(8)
        self._step_labels = []
        for step, text in _STEP_LABELS:
            lab = QLabel(text)
            lab.setObjectName("StepIdle")
            lab.setAlignment(Qt.AlignCenter)
            row.addWidget(lab, 1)
            self._step_labels.append(lab)
        step_layout.addLayout(row)
        layout.addWidget(step_card)

        # 状态
        status_card = QFrame()
        status_card.setObjectName("Card")
        status_layout = QHBoxLayout(status_card)
        status_layout.setContentsMargins(12, 10, 12, 10)
        self._status_label = QLabel("状态：空闲 · 等待操作")
        self._status_label.setObjectName("StatusValue")
        self._request_label = QLabel("request_id：—")
        self._request_label.setObjectName("Hint")
        status_layout.addWidget(self._status_label, 2)
        status_layout.addWidget(self._request_label, 1)
        layout.addWidget(status_card)

        # 操作 + 清单
        mid = QHBoxLayout()
        mid.setSpacing(10)

        ops_card = QFrame()
        ops_card.setObjectName("Card")
        ops = QVBoxLayout(ops_card)
        ops.setContentsMargins(12, 10, 12, 12)
        ops.setSpacing(8)
        ops.addWidget(self._section("操作"))
        self._btn_device_init = QPushButton("1. 设备初始化")
        self._btn_device_init.setObjectName("PrimaryBtn")
        self._btn_device_init.clicked.connect(self._on_device_init)
        self._btn_material_init = QPushButton("2. 物料初始化")
        self._btn_material_init.clicked.connect(self._on_material_init)
        self._btn_dose_request = QPushButton("3. 申请加料")
        self._btn_dose_request.setObjectName("PrimaryBtn")
        self._btn_dose_request.clicked.connect(self._on_dose_request)
        self._btn_dose_confirm = None
        self._btn_bottle_placed = QPushButton("4. 已放瓶（开始加样）")
        self._btn_bottle_placed.setObjectName("PrimaryBtn")
        self._btn_bottle_placed.clicked.connect(self._on_bottle_placed)
        self._btn_bottle_removed = QPushButton("5. 已取瓶（回空闲）")
        self._btn_bottle_removed.setObjectName("PrimaryBtn")
        self._btn_bottle_removed.clicked.connect(self._on_bottle_removed)
        self._btn_abort = QPushButton("中止")
        self._btn_abort.setObjectName("DangerBtn")
        self._btn_abort.clicked.connect(self._on_abort)
        for btn in (
            self._btn_device_init,
            self._btn_material_init,
            self._btn_dose_request,
            self._btn_bottle_placed,
            self._btn_bottle_removed,
            self._btn_abort,
        ):
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            ops.addWidget(btn)
        ops.addStretch(1)
        mid.addWidget(ops_card, 2)

        dose_card = QFrame()
        dose_card.setObjectName("Card")
        dose_layout = QVBoxLayout(dose_card)
        dose_layout.setContentsMargins(12, 10, 12, 12)
        dose_layout.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(self._section("加料清单（单位 mg）"))
        head.addStretch(1)
        btn_add = QPushButton("添加行")
        btn_add.clicked.connect(self._on_add_row)
        btn_del = QPushButton("删除行")
        btn_del.clicked.connect(self._on_del_row)
        head.addWidget(btn_add)
        head.addWidget(btn_del)
        dose_layout.addLayout(head)
        self._dose_table = QTableWidget(0, 3)
        self._dose_table.setHorizontalHeaderLabels(["物料条码", "目标(mg)", "实际(mg)"])
        self._dose_table.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
        self._dose_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._dose_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self._dose_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self._dose_table.setColumnWidth(1, 100)
        self._dose_table.setColumnWidth(2, 100)
        self._dose_table.verticalHeader().setVisible(False)
        self._dose_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._dose_table.setSelectionMode(QAbstractItemView.SingleSelection)
        dose_layout.addWidget(self._dose_table)
        self._append_dose_row("111", "1", "")
        mid.addWidget(dose_card, 3)
        layout.addLayout(mid, 2)

        # 日志
        log_card = QFrame()
        log_card.setObjectName("Card")
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(12, 10, 12, 12)
        log_head = QHBoxLayout()
        log_head.addWidget(self._section("通信日志"))
        log_head.addStretch(1)
        btn_clear = QPushButton("清空日志")
        btn_clear.clicked.connect(lambda: self._log_view.clear() if self._log_view else None)
        log_head.addWidget(btn_clear)
        log_layout.addLayout(log_head)
        self._log_view = QPlainTextEdit()
        self._log_view.setObjectName("Log")
        self._log_view.setReadOnly(True)
        self._log_view.setMinimumHeight(180)
        font = QFont("Consolas", 10)
        self._log_view.setFont(font)
        log_layout.addWidget(self._log_view)
        layout.addWidget(log_card, 2)

    @staticmethod
    def _section(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("Title")
        return lab

    def _append_dose_row(
        self, barcode: str = "", target: str = "", actual: str = ""
    ) -> None:
        if self._dose_table is None:
            return
        row = self._dose_table.rowCount()
        self._dose_table.insertRow(row)
        for col, text in enumerate((barcode, target, actual)):
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignCenter)
            if col == 2:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._dose_table.setItem(row, col, item)

    def _on_add_row(self) -> None:
        self._append_dose_row("", "", "")

    def _on_del_row(self) -> None:
        if self._dose_table is None:
            return
        row = self._dose_table.currentRow()
        if row >= 0:
            self._dose_table.removeRow(row)

    def _collect_items(self) -> list[dict]:
        items: list[dict] = []
        if self._dose_table is None:
            return items
        for row in range(self._dose_table.rowCount()):
            code_item = self._dose_table.item(row, 0)
            weight_item = self._dose_table.item(row, 1)
            code = (code_item.text() if code_item else "").strip()
            weight_text = (weight_item.text() if weight_item else "").strip()
            if not code and not weight_text:
                continue
            try:
                target = float(weight_text) if weight_text else 0.0
            except ValueError:
                target = 0.0
            items.append({"barcode": code, "target_mg": target})
        return items

    def _set_actuals(self, items: list) -> None:
        if self._dose_table is None or not isinstance(items, list):
            return
        self._dose_table.setRowCount(0)
        for raw in items:
            if not isinstance(raw, dict):
                continue
            barcode = str(raw.get("barcode") or "")
            target = raw.get("target_mg")
            actual = raw.get("actual_mg")
            t = "" if target is None else f"{float(target):g}"
            a = "" if actual is None else f"{float(actual):g}"
            self._append_dose_row(barcode, t, a)

    def _append_log(self, text: str) -> None:
        if self._log_view is None:
            return
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_view.appendPlainText(f"[{ts}] {text}")

    def _on_udp_log(self, text: str) -> None:
        self.log_arrived.emit(text)

    def _on_udp_event(self, payload: dict) -> None:
        self.event_arrived.emit(payload)

    def _set_step(self, step: SimStep, status: str) -> None:
        self._step = step
        if self._status_label is not None:
            self._status_label.setText(f"状态：{status}")
        if self._request_label is not None:
            rid = self._request_id or "—"
            self._request_label.setText(f"request_id：{rid}")
        self._refresh_step_ui()

    def _refresh_step_ui(self) -> None:
        current = self._step
        for i, (step, _text) in enumerate(_STEP_LABELS):
            lab = self._step_labels[i]
            if step == current:
                lab.setObjectName("StepCurrent")
            elif step.value < current.value:
                lab.setObjectName("StepDone")
            else:
                lab.setObjectName("StepIdle")
            lab.style().unpolish(lab)
            lab.style().polish(lab)

        if self._btn_device_init:
            self._btn_device_init.setEnabled(current == SimStep.IDLE)
        if self._btn_material_init:
            self._btn_material_init.setEnabled(current == SimStep.IDLE)
        if self._btn_dose_request:
            self._btn_dose_request.setEnabled(current == SimStep.IDLE)
        if self._btn_bottle_placed:
            self._btn_bottle_placed.setEnabled(current == SimStep.WAIT_BOTTLE)
        if self._btn_bottle_removed:
            self._btn_bottle_removed.setEnabled(current == SimStep.WAIT_PICKUP)
        if self._btn_abort:
            self._btn_abort.setEnabled(current != SimStep.IDLE)
        if self._dose_table is not None:
            editable = current == SimStep.IDLE
            self._dose_table.setEnabled(editable)

    def _handle_event(self, payload: dict) -> None:
        cmd = str(payload.get("cmd") or "")
        status = str(payload.get("status") or "")
        message = str(payload.get("message") or "")
        rid = str(payload.get("request_id") or self._request_id)
        if rid:
            self._request_id = rid

        if cmd == CMD_DEVICE_INIT_DONE:
            if status == "success":
                self._set_step(SimStep.IDLE, f"设备初始化完成 · {message}")
            else:
                self._set_step(SimStep.IDLE, f"设备初始化失败 · {message}")
            return
        if cmd == CMD_MATERIAL_INIT_DONE:
            if status == "success":
                self._set_step(SimStep.IDLE, f"物料初始化完成 · {message}")
            else:
                self._set_step(SimStep.IDLE, f"物料初始化失败 · {message}")
            return
        if cmd == CMD_DOSE_COMPLETE:
            items = payload.get("items") or []
            self._set_actuals(items if isinstance(items, list) else [])
            if status == "success":
                self._set_step(SimStep.WAIT_PICKUP, f"加样完成，等待取瓶 · {message}")
            else:
                self._set_step(SimStep.IDLE, f"加样失败 · {message}")
            return

        self._append_log(f"未处理事件 cmd={cmd} status={status}")

    def _on_device_init(self) -> None:
        self._request_id = HostUdpClient.new_request_id()
        self._set_step(SimStep.DEVICE_INIT, "设备初始化中…")
        resp = self._client.send_cmd(
            {"cmd": CMD_DEVICE_INIT_REQUEST, "request_id": self._request_id}
        )
        if resp.get("status") != "accepted":
            self._set_step(SimStep.IDLE, f"设备初始化被拒绝 · {resp.get('message')}")
            QMessageBox.warning(self, "设备初始化", str(resp.get("message") or resp))

    def _on_material_init(self) -> None:
        self._request_id = HostUdpClient.new_request_id()
        self._set_step(SimStep.MATERIAL_INIT, "物料初始化中…")
        resp = self._client.send_cmd(
            {"cmd": CMD_MATERIAL_INIT_REQUEST, "request_id": self._request_id}
        )
        if resp.get("status") != "accepted":
            self._set_step(SimStep.IDLE, f"物料初始化被拒绝 · {resp.get('message')}")
            QMessageBox.warning(self, "物料初始化", str(resp.get("message") or resp))

    def _on_dose_request(self) -> None:
        items = self._collect_items()
        if not items:
            QMessageBox.warning(self, "申请加料", "请先填写加料清单。")
            return
        self._request_id = HostUdpClient.new_request_id()
        self._set_step(SimStep.DOSE_APPLY, "加料申请中…")
        resp = self._client.send_cmd(
            {
                "cmd": CMD_DOSE_REQUEST,
                "request_id": self._request_id,
                "items": items,
            }
        )
        if resp.get("status") == "accepted":
            # 加样侧已自动响应并进入等待进瓶 → 本侧直接进瓶
            self._set_step(
                SimStep.WAIT_BOTTLE,
                f"已响应申请，请进瓶 · {resp.get('message')}",
            )
        else:
            self._set_step(SimStep.IDLE, f"不响应申请 · {resp.get('message')}")
            self._request_id = ""
            QMessageBox.warning(self, "申请加料", str(resp.get("message") or resp))

    def _on_bottle_placed(self) -> None:
        resp = self._client.send_cmd(
            {"cmd": CMD_BOTTLE_PLACED, "request_id": self._request_id}
        )
        if resp.get("status") in ("ok", "success", "accepted"):
            self._set_step(SimStep.DOSING, "加样进行中…")
        else:
            QMessageBox.warning(self, "已放瓶", str(resp.get("message") or resp))

    def _on_bottle_removed(self) -> None:
        resp = self._client.send_cmd(
            {"cmd": CMD_BOTTLE_REMOVED, "request_id": self._request_id}
        )
        if resp.get("status") in ("ok", "success", "accepted"):
            self._set_step(SimStep.IDLE, "取瓶完成 · 已回空闲")
            self._request_id = ""
            if self._request_label is not None:
                self._request_label.setText("request_id：—")
        else:
            QMessageBox.warning(self, "已取瓶", str(resp.get("message") or resp))

    def _on_abort(self) -> None:
        resp = self._client.send_cmd(
            {"cmd": CMD_ABORT, "request_id": self._request_id}
        )
        self._set_step(SimStep.IDLE, f"已请求中止 · {resp.get('message')}")
        self._request_id = ""
